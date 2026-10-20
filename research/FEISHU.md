# 从 GitHub 自动发布纯分析到飞书

本仓库使用独立的 [Publish research to Feishu 工作流](../.github/workflows/publish-feishu.yml)，把通过校验的研究 JSON 转成飞书原生文档块和趋势图。数据来源仍是 `research/reports/`；GitHub Pages 与飞书各自发布、各自验收。飞书应用凭据由仓库管理员在 GitHub 中配置，不写进代码或报告。

飞书发布器会在指定 Wiki 入口下建立独立的“复盘分析（自动发布）”分支，再按标的和报告日期组织。它只管理自己创建并标识的页面，保留入口原文和原有子页面。历史报告沿用 [JSON 合同](CONTRACT.md) 的 `symbol + trade_date + revision` 身份，继续明确区分行情截止、实际生成时间及历史补录。

## 首次使用：先校验，再检查连接，再发布

1. 在飞书开放平台创建企业自建应用，按下节申请应用身份权限，发布应用并由企业管理员启用；为目标 Wiki 入口授予应用所需的资源权限。
2. 在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 配置三个 Repository secrets，见配置表。推荐直接把完整 Wiki 入口 URL 填入 `FEISHU_WIKI_ROOT_TOKEN`。
3. 打开仓库 **Actions → Publish research to Feishu → Run workflow**，分支选 `main`，模式先选 `validate`。这一阶段不读取飞书凭据、不调用飞书 API。
4. 再以 `check` 模式运行。它获取应用凭证，解析入口，读取 Wiki 节点及其子节点，检查连接和读取能力；不创建或编辑文档。**check 成功不代表已经具有创建、编辑或上传图片的权限。**
5. 最后以 `publish` 模式运行。首次可在 `symbol` 填一个登记代码，例如 `002594.SZ`，缩小实际验收范围；确认自动分支、标的页、报告页、图表及跳转正确，再留空 `symbol` 发布全部。

默认手动模式为 `check`。`check` 和 `publish` 必须从 `main` 发起；其他分支只允许离线 `validate`，不能取得本工作流的飞书凭据。

## GitHub 配置

### Repository secrets

| 名称 | 必填 | 内容 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 是 | 飞书自建应用的 App ID。 |
| `FEISHU_APP_SECRET` | 是 | 同一自建应用的 App Secret。 |
| `FEISHU_WIKI_ROOT_TOKEN` | 是 | 推荐填完整 Wiki 入口 URL；也接受入口 URL 末尾的节点 Token。它是允许创建自动发布分支的父节点，不是某份报告的 docx ID。 |

完整 URL 采用 `https://<你的租户>.feishu.cn/wiki/<节点Token>` 格式，填写真实值时不保留尖括号。脚本从完整 URL 提取节点 Token 与租户 origin；文档和代码中不预置任何用户的私人地址。

`FEISHU_WIKI_ROOT_TOKEN` 也兼容同名 Repository variable，Secret 优先。推荐使用 Secret，避免私人入口被当作公开配置传播。只填裸 Token 时，必须同时配置下表中的 `FEISHU_WIKI_BASE_URL`，脚本不会猜测租户域名。

这里只配置应用级 App ID 与 App Secret，**没有让用户粘贴单次 `tenant_access_token` 的配置项**。临时凭证由每次运行按需申请，并按响应有效期管理；App Secret 轮换后应同步更新仓库 Secret。

### 可选 Repository variables

| 名称 | 何时使用 | 内容 |
| --- | --- | --- |
| `FEISHU_WIKI_BASE_URL` | 入口只填裸 Token 时必填 | 租户 origin，格式为 `https://<你的租户>.feishu.cn`，不含 `/wiki/...` 路径。完整入口 URL 已包含该信息时可不填。 |
| `RESEARCH_PAGES_URL` | 需要从飞书跳回 Pages 时 | 研究站公开根地址，用于总览和标的页跳回公开站点；留空时不生成这些回链。历史报告保留稳定的飞书层级导航，不因这个配置改变正文。不得填私人飞书或 Notion 地址。 |
| `FEISHU_ROOT_TITLE` | 需要自定义自动分支标题时 | 默认 `复盘分析（自动发布）`。首次发布后应保持稳定；它参与识别自动分支，改名可能指向另一个分支。 |

请直接在 GitHub 配置界面填写凭据和私人入口，不把它们提交到仓库、Issue、报告 JSON 或工作流源码。工作流不会上传包含飞书地址或 Token 的 artifact，也不会把私人入口写入运行摘要。

## 飞书应用权限与资源授权

飞书的 **API 权限**与**具体文档／知识库资源权限**需要分别具备。只拿到应用 Token，或只在开发者后台勾选权限，都不足以证明可以写入某个 Wiki。

本版直接从已校验 JSON 生成原生文档块，不调用 Markdown 转换 API，因此无需为本版额外开通 Markdown/HTML 转换权限。以下按官方接口页的权限名称配置最小能力；后台权限名称可能显示中英文，范围应选择支持**应用身份**的项。

| 能力 | 官方接口页列出的对应权限 | 用途与官方依据 |
| --- | --- | --- |
| 读取 Wiki 入口 | 查看知识空间节点信息 | 解析 `node_token`、`space_id`、`obj_token` 与类型；见[获取知识空间节点信息](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node?lang=zh-CN)。 |
| 列出子节点 | 查看知识空间节点列表 | 查找已有自动分支、标的及报告节点，支持幂等查找；见[获取知识空间子节点列表](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/list?lang=zh-CN)。 |
| 创建层级节点 | 创建知识空间节点 / Create wiki space node | 在允许的父节点下创建 docx 子节点；见[创建知识空间节点](https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/create?lang=zh-CN)。 |
| 读取新版文档内容 | 查看新版文档 | 检查发布器标记、已写正文与实际结果；见[获取文档所有块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/list?lang=zh-CN)。 |
| 编辑新版文档 | 编辑新版文档；接口也接受创建及编辑新版文档 | 创建、更新和清理发布器管理的块，设置图片素材；见[创建块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create?lang=zh-CN)及[更新块的内容](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/patch?lang=zh-CN)。 |
| 上传文档图片素材 | Upload image and file to document（向文档上传图片与文件） | 上传趋势图到文档图片块；见[上传素材](https://open.feishu.cn/document/ukTMukTMukTM/uYjM5YjL2ITO24iNykjN)。这是文档素材上传，不是聊天消息图片上传。 |

上述 Wiki 接口也接受较宽的“查看、编辑和管理知识库”权限，官方[知识库概述](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview?lang=zh-CN)列出其标识为 `wiki:wiki`。优先按当前后台提供的细粒度项开通实际所需能力，不必为此申请整个云空间所有文件的管理权限。这里没有猜写未从官方页面确认的细粒度 scope 字符串；需要批量导入权限时，应从开发者后台复制当前权限标识。

权限开通后还需发布正式应用版本、由企业管理员启用。只在测试版本里开通并不等于正式版本已生效，参见官方[申请 API 权限与发布步骤](https://open.feishu.cn/document/faq/trouble-shooting/how-to-fix-the-99991672-error?lang=zh-CN)。

在目标 Wiki 中，将应用添加为有相应权限的成员或文档协作者，并确保：

- `check` 至少能读取入口与列出子节点。
- `publish` 需要入口的**容器编辑权限**，才能创建自动分支；单页面编辑权限不允许创建子节点。
- 应用能编辑自动分支中的 docx，并能向这些文档上传素材。

这一区分来自官方[知识库权限说明](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview?lang=zh-CN)。无需把应用升级为整个知识库管理员才能表达本方案所需权限；实际授权范围由管理员按目标节点配置。

## 三种运行模式与触发行为

| 运行方式 | 凭据 | 实际行为 | 配置缺失时 |
| --- | --- | --- | --- |
| 手动 `validate` | 不加载 | 运行单元测试、研究合同校验和飞书内容离线渲染检查。 | 仍可执行；与飞书连接无关。 |
| Pull request，包括 fork PR | 不加载 | 同上，仅校验；不进入 API 作业。 | 仍可执行；不会因没有 Secrets 而尝试真实发布。 |
| 手动 `check`，仅 main | 需要 | OAuth 鉴权及读取目标 Wiki／子节点；不写文档。 | 明确失败，并指出缺少的配置名称，不回显值。 |
| 手动 `publish`，仅 main | 需要 | 校验通过后，创建或更新自动分支范围内的内容，并读取结果。 | 明确失败，不标记发布成功。 |
| main 的相关文件 push | 已配置时需要 | 先离线校验，再自动 `publish`；这不依赖是否手动运行过。 | 跳过飞书 API 调用，运行摘要提示配置；不能把这种跳过当作写入成功。 |

`symbol` 留空表示全部登记标的；填值时只选择对应标的，必须是 `symbols.json` 内的完整代码。它是发布范围筛选，不用于绕过完整数据集校验。

自动触发范围包括 `research/**`、飞书发布脚本、研究校验器、相关测试、`requirements-feishu.txt` 及工作流自身。因此把凭据配置齐全后，后续这些文件进入 `main` 就会尝试同步。工作流不创建定时任务；如由已有或新建 Task 提交报告 JSON，提交仍按相同机制触发。

飞书工作流与 Pages 分开。飞书失败不会回滚已发布的 Pages，Pages 成功也不能证明飞书写入成功。真实飞书 API 作业共享串行并发组 `feishu-publisher-main`，不主动取消正在进行的发布；PR 和离线校验不参与该队列，避免它们取代等待发布的作业。并发组不保证触发顺序，因此 API 作业开始时拉取最新 `main`，在加载飞书凭据前重新校验当前数据，有事件基准时继续检查原报告未被覆盖或删除。发布器再根据这些数据和飞书已有内容判断需补齐的工作，不依赖“每个 push 都恰好运行一次”。

## 命令行使用

从仓库根目录执行，Python 版本为 3.12：

```bash
python -m pip install -r requirements-feishu.txt
python -m unittest discover -s tests -p 'test_*.py'
python scripts/validate_research.py --root .
python scripts/publish_feishu.py --root . --mode validate
```

下面两种模式要求运行环境已经安全注入上述配置；命令中不传入 Secret 或 Token：

```bash
python scripts/publish_feishu.py --root . --mode check
python scripts/publish_feishu.py --root . --mode publish --symbol 002594.SZ
python scripts/publish_feishu.py --root . --mode publish
```

对有完整 Git 历史的后续提交，工作流还运行 `validate_research.py --base-ref <事件基准提交SHA>`，阻止覆盖或删除原始报告。首次或手动运行没有基准时执行完整快照校验。修订研究报告应新增 `.v2.json` 等版本，不覆盖原 JSON。

## 应用凭证采用新版 OAuth

发布器使用官方 2026-10-14 更新的[自建应用获取 tenant_access_token](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/authentication-management/access-token/tenant_access_token)接口：

```text
POST https://accounts.feishu.cn/oauth/v3/token
grant_type=client_credentials
client_id=<App ID>
client_secret=<App Secret>
```

成功响应在顶层返回 `code: 0`、`access_token`、`expires_in`、`token_type: Bearer`。其中 `access_token` 代表应用身份的 tenant access token，后续 API 仍用 Bearer 鉴权。凭证最长两小时，实际刷新时间以 `expires_in` 为准；新版 JWT 更长，不能依赖旧 Token 前缀或固定长度。

旧 `auth/v3/tenant_access_token/internal` 使用 `app_id/app_secret`、返回 `tenant_access_token/expire`，官方已列为[历史版本，不推荐](https://open.feishu.cn/document/ukTMukTMukTM/uIjNz4iM2MjLyYzM)。本版不把旧接口响应字段或旧的 30 分钟再发规则套用到新版。

App Secret 只在运行服务端使用。调试时不要打印完整 OAuth 响应、环境变量、Authorization 头或请求对象；这些内容可能包含凭证。也不要把客户端密钥塞进前端 Pages。

## 层级、幂等与中断恢复

自动分支下包括总览、标的节点以及日期／修订报告。报告页保留分析、公开来源、限制和真实价格历史；趋势图由这些公开分析数据生成。私人 Wiki Token 与文档链接只在飞书和当前发布进程内使用，不反向写入公开报告。

发布器用业务身份和稳定管理标记识别自己管理的页面，而不是单凭“标题看起来相同”就接管内容：

- 总览、标的与日期-vN 节点有确定的身份；标的改显示名称时仍按完整代码识别。历史报告使用稳定的代码身份，展示名称及可选 Pages 回链的变化只影响可更新的总览和标的页。
- 已存在且正文完整相同的历史报告跳过；只有尚未写入完成尾标记、且正文是期望内容前缀的报告，才允许从中断位置续写。完整报告末尾追加内容也属于修改旧版本，应新增 JSON 修订。
- 已完整发布、同一身份但内容不同的历史报告会报错，要求新增研究 JSON 修订；不会覆盖旧报告。
- 同名页面若非空且缺少发布器识别的管理标记，会拒绝接管，原文保留。
- 飞书新建文档自带的纯空白段落按空文档处理，识别并校验后才清理和写入管理标记；有正文的未管理页面不适用这一规则。
- 总览与标的页可随新报告更新：先追加新正文与图片、读回核验，再分批清理旧内容。管理标记使中断后的再次运行能够判断续写或清理位置。

文档块写请求在同一批重试时复用 `client_token`；官方[创建块](https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document-block/create?lang=zh-CN)支持该幂等参数。Wiki 节点创建若发生结果不确定的错误，会先重新列出子节点查找精确身份，不盲目再建一份。

图片上传存在独立的失败边界：不确定结果可能留下未绑定素材，后续运行可重试占位图片的补齐；本方案不承诺媒体上传严格只发生一次。官方没有在上述 docx 页面给出 `client_token` 去重保留时长，因此业务去重仍依靠页面身份、管理标记和读回比较，不把请求 Token 当作永久去重记录。

请把人工评论或附加内容放在自动分支之外。发布器管理的总览／标的正文会随数据更新，历史报告则按修订冻结；人工改动可能使精确内容比对失败。

## 怎么判断真的完成

| 证据 | 能证明什么 |
| --- | --- |
| 本地或 CI 单元测试、离线 `validate` 通过 | 代码逻辑、合同检查和模拟场景通过；没有验证真实凭据或飞书写权限。 |
| `check` 成功 | 应用能够鉴权并读取配置的 Wiki 入口及子节点；不证明创建、编辑或图片上传能力。 |
| `publish` 成功且发布器读回核验通过 | 对本次选定范围，真实飞书创建／编辑与内容核对已完成。 |
| 在飞书实际查看层级与图片 | 确认总览、标的、报告和跳转符合阅读需求；以后每次运行仍需检查各自结果。 |
| Task 实际提交了 JSON，随后飞书工作流成功 | 才能认定该次“后台 Task → GitHub → 飞书”链路实际完成。 |

本地模拟测试不是实际飞书写入。无凭据的自动跳过也不是连接成功。连接、内容写入、图片显示以及后台 Task 是不同验收阶段，运行结果应分别描述。

Task 仍需自己具备目标 GitHub 仓库的写入工具与权限，并显式遵守本仓库分析合同。这个工作流不会自动继承旧对话、Notion 内容、旧任务说明或会话里的凭据，也不会创建或修改任何 ChatGPT Task。

## 常见失败的处理

| 情况 | 处理 |
| --- | --- |
| push 显示配置缺失并跳过 | 补齐 Repository secrets；裸 Token 还需租户 origin。然后手动从 main 运行 `check`。 |
| 手动 check/publish 配置不完整 | 运行会失败，按摘要中的配置名称补齐；不要把真实值贴进日志。 |
| 鉴权失败 | 核对 App ID/Secret 是否属于同一个已发布且启用的应用；轮换密钥后更新 Secret。新版解析字段必须是 `access_token/expires_in`。 |
| check 通过，但创建节点被拒绝 | 核对创建节点 API 权限及父节点容器编辑权限；只有读取或单页面编辑权限不够。 |
| 正文可写，趋势图失败 | 核对文档素材上传权限及目标 docx 的编辑权限；不要改用聊天图片接口。修复后重跑对应标的。 |
| 历史报告内容冲突 | 保留原报告，为新内容创建下一连续 JSON 修订；不要清空原文来强行绕过检查。 |
| 同名未管理页面冲突 | 不接管原页面。确认所配入口与自动分支标题，选择没有身份冲突的独立自动分支。 |
| 频控或网络不确定错误 | 保留当前内容，按发布器的退避及恢复逻辑重试；避免同时启动另一个本地发布器写同一分支。 |

遇到权限错误时区分 API 权限、应用启用状态与资源授权，参见官方[权限申请说明](https://open.feishu.cn/document/faq/trouble-shooting/how-to-fix-the-99991672-error?lang=zh-CN)及[知识库权限说明](https://open.feishu.cn/document/server-docs/docs/wiki-v2/wiki-overview?lang=zh-CN)。排障只记录阶段、业务身份、错误码与脱敏状态，不传播私人入口或凭据。
