#!/usr/bin/env python3
"""Small local tool harness for OpenAI-compatible Chat Completions providers.

The provider only sees the prompt, selected file contents and tool results. The API
key stays in this process; commands run with it removed from their environment.
"""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

API_KEY = os.environ.pop("QUANT_STUDIO_API_KEY")
API_BASE = os.environ.pop("QUANT_STUDIO_API_ENDPOINT").rstrip("/")
MODEL = os.environ.pop("QUANT_STUDIO_API_MODEL")
PRINT_LOCK = threading.Lock()
INPUT_LOCK = threading.Lock()
MAX_TURNS = 100


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


HTTP = urllib.request.build_opener(NoRedirect)


def say(message):
    with PRINT_LOCK:
        print(message, flush=True)


def api_url():
    return API_BASE if API_BASE.endswith("/chat/completions") else API_BASE + "/chat/completions"


def request_chat(messages, tools):
    data = json.dumps({"model": MODEL, "messages": messages, "tools": tools,
                       "tool_choice": "auto"}).encode()
    request = urllib.request.Request(api_url(), data=data, headers={
        "Authorization": "Bearer " + API_KEY, "Content-Type": "application/json"}, method="POST")
    try:
        with HTTP.open(request, timeout=180) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("模型请求失败：HTTP %s；检查模型、协议和余额" % error.code) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("模型连接失败：%s" % error) from None
    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0].get("message"), dict):
        raise RuntimeError("服务商未返回 Chat Completions 消息")
    return choices[0]["message"]


def tool(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


S = lambda description: {"type": "string", "description": description}
TOOLS = [
    tool("read_file", "Read a workspace text file (up to 100 KB).", {"path": S("Absolute path or workspace-relative path")}, ["path"]),
    tool("write_file", "Write a text file inside the current job directory. Use this for workflow JSON and reports.",
         {"path": S("Absolute path or job-relative path"), "content": S("Complete file content")}, ["path", "content"]),
    tool("run_command", "Run a shell command in workspace after explicit approval in the terminal. Use for data clients and report renderer.",
         {"command": S("Command to run"), "timeout_seconds": {"type": "integer", "description": "Maximum 180 seconds"}}, ["command"]),
    tool("fetch_url", "Fetch a public HTTPS page (up to 100 KB) for first-party source verification.",
         {"url": S("HTTPS URL")}, ["url"]),
    tool("delegate_many", "Run independent, isolated subagents in parallel and wait for all. Required for company-deep-dive 2 + 4 + 1 phases.",
         {"tasks": {"type": "array", "items": {"type": "object", "properties": {
             "name": S("Short agent name"), "prompt": S("Full self-contained task including output file")},
             "required": ["name", "prompt"]}, "maxItems": 4}}, ["tasks"]),
]


class Harness:
    def __init__(self, workspace, job):
        self.workspace = workspace.resolve()
        self.job = job.resolve()

    def safe_path(self, raw, write=False):
        path = Path(raw)
        path = (path if path.is_absolute() else (self.job if write else self.workspace) / path).resolve()
        allowed = self.job if write else self.workspace
        if not path.is_relative_to(allowed):
            raise ValueError("路径必须位于%s" % allowed)
        if not write and any(part in {".local", ".git", "secrets", "terminal.log", "cli-settings.json", "longbridge.json"}
                             or part.startswith(".env") for part in path.parts):
            raise ValueError("不能读取凭据或内部目录")
        return path

    def approve(self, command):
        with INPUT_LOCK:
            say("\n需要运行命令（输入 y 确认，其他键拒绝）：\n%s" % command)
            return sys.stdin.readline().strip().lower() in ("y", "yes")

    def call(self, name, args, can_delegate=True):
        if name == "read_file":
            path = self.safe_path(args["path"])
            if path.stat().st_size > 100_000:
                raise ValueError("文件超过 100 KB，请用命令筛选所需内容")
            return path.read_text(errors="replace")
        if name == "write_file":
            path = self.safe_path(args["path"], write=True)
            content = args["content"]
            if len(content.encode()) > 2_000_000:
                raise ValueError("单文件超过 2 MB")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            say("已写入 %s" % path.relative_to(self.job))
            return "已写入 %s（%s 字节）" % (path, len(content.encode()))
        if name == "run_command":
            command = args["command"]
            if not self.approve(command):
                return "用户拒绝执行命令"
            timeout = min(max(int(args.get("timeout_seconds", 120)), 1), 180)
            env = {key: value for key, value in os.environ.items() if key != "QUANT_STUDIO_API_KEY"}
            try:
                result = subprocess.run(command, shell=True, cwd=self.workspace, env=env,
                                        text=True, capture_output=True, timeout=timeout)
                return "exit=%s\n%s%s" % (result.returncode, result.stdout[-12000:], result.stderr[-4000:])
            except subprocess.TimeoutExpired:
                return "命令超时"
        if name == "fetch_url":
            from urllib.parse import urlparse
            url = args["url"]
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("只允许公开 HTTPS URL")
            request = urllib.request.Request(url, headers={"User-Agent": "QuantLabResearch/1.0"})
            with HTTP.open(request, timeout=25) as response:
                return response.read(100_000).decode("utf-8", errors="replace")
        if name == "delegate_many" and can_delegate:
            tasks = args["tasks"]
            if not 1 <= len(tasks) <= 4:
                raise ValueError("一次委派 1–4 个子代理")
            def run_sub(task):
                say("子代理启动：%s" % task["name"])
                result = self.run(task["prompt"], can_delegate=False, label=task["name"])
                say("子代理结束：%s" % task["name"])
                return {"name": task["name"], "result": result}
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                return json.dumps(list(pool.map(run_sub, tasks)), ensure_ascii=False)
        raise ValueError("未知或不可用工具：%s" % name)

    def run(self, prompt, can_delegate=True, label="主代理"):
        system = ("你是 Quant Lab 的研究代理。严格执行用户指定 SKILL.md 全流程；真实数据和来源必须可追溯，"
                  "禁止伪造产物。可用 read_file、write_file、run_command、fetch_url。"
                  "公司分析阶段必须用 delegate_many 启动隔离子代理：先 2 个数据代理，再 4 个分析代理，"
                  "再 1 个 DCF 代理；每阶段等待完成。子代理提示必须完整自包含。"
                  "所有输出写到指定任务目录，最终 HTML 按 skill 要求运行渲染脚本。"
                  "若数据、授权或工具不可用，清楚报告阻碍，不得声称完成。")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        tools = TOOLS if can_delegate else TOOLS[:-1]
        for step in range(MAX_TURNS):
            response = request_chat(messages, tools)
            content = response.get("content") or ""
            calls = response.get("tool_calls") or []
            if content:
                say("[%s] %s" % (label, content))
            if not calls:
                return content
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                function = call.get("function") or {}
                name = function.get("name", "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    result = self.call(name, arguments, can_delegate)
                    if not isinstance(result, str):
                        result = json.dumps(result, ensure_ascii=False)
                except Exception as error:
                    result = "工具失败：%s" % error
                    say("[%s] %s" % (label, result))
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": result[:14000]})
        raise RuntimeError("达到最大工具轮数，任务未完成")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--job-dir", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, required=True)
    args = parser.parse_args()
    prompt = args.prompt_file.read_text()
    say("自定义 Chat Completions 工作流已启动 · 模型 %s" % MODEL)
    Harness(args.workspace, args.job_dir).run(prompt)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("任务已中断")
        sys.exit(130)
    except Exception as error:
        say("任务失败：%s" % error)
        sys.exit(1)
