#[path = "../../src/factors.rs"]
mod factors;

use factors::{Factor, FactorLib};
use gpui_kit::component::button::Button;
use gpui_kit::component::input::{Input, InputEvent, InputState};
use gpui_kit::component::*;
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use std::collections::HashSet;
use std::path::PathBuf;

const fn color(hex: u32) -> Rgba {
    Rgba {
        r: ((hex >> 16) & 0xff) as f32 / 255.0,
        g: ((hex >> 8) & 0xff) as f32 / 255.0,
        b: (hex & 0xff) as f32 / 255.0,
        a: 1.0,
    }
}

const BG: Rgba = color(0x0b111b);
const RAIL: Rgba = color(0x0e1723);
const PANEL: Rgba = color(0x141f2e);
const HOVER: Rgba = color(0x1d2d3e);
const LINE: Rgba = color(0x29394c);
const TEXT: Rgba = color(0xf1f5f8);
const MUTED: Rgba = color(0x9babbc);
const GREEN: Rgba = color(0xb8ee80);
const BLUE: Rgba = color(0x83c4fb);
const AMBER: Rgba = color(0xf4c982);

#[derive(Clone, Copy, PartialEq, Eq)]
enum Direction {
    All,
    Long,
    Short,
}

struct QuantLab {
    path: PathBuf,
    factors: Vec<(String, Factor)>,
    selected: Option<String>,
    direction: Direction,
    implemented_only: bool,
    search: Entity<InputState>,
    _search_subscription: Subscription,
    error: Option<String>,
}

impl QuantLab {
    fn new(window: &mut Window, cx: &mut Context<Self>) -> Self {
        let search = cx.new(|cx| InputState::new(window, cx).placeholder("搜索名称、ID 或说明"));
        let subscription = cx.subscribe(&search, |this, _, event: &InputEvent, cx| {
            if matches!(event, InputEvent::Change) {
                this.selected = None;
                cx.notify();
            }
        });
        let mut app = Self {
            path: PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../auto-trader/strategy-kit/strategy_kit.json"),
            factors: Vec::new(),
            selected: None,
            direction: Direction::All,
            implemented_only: false,
            search,
            _search_subscription: subscription,
            error: None,
        };
        app.reload();
        app
    }

    fn reload(&mut self) {
        match FactorLib::load(&self.path) {
            Ok(lib) => {
                self.factors = lib.blocks.into_iter().collect();
                self.factors.sort_by(|a, b| a.0.cmp(&b.0));
                if !self
                    .factors
                    .iter()
                    .any(|(id, _)| self.selected.as_ref() == Some(id))
                {
                    self.selected = None;
                }
                self.error = None;
            }
            Err(error) => self.error = Some(format!("{error:#}")),
        }
    }

    fn filtered<'a>(&'a self, query: &str) -> Vec<&'a (String, Factor)> {
        let query = query.trim().to_lowercase();
        self.factors
            .iter()
            .filter(|(id, factor)| {
                (query.is_empty()
                    || id.to_lowercase().contains(&query)
                    || factor.name.to_lowercase().contains(&query)
                    || factor.description.to_lowercase().contains(&query))
                    && match self.direction {
                        Direction::All => true,
                        Direction::Long => factor.direction == "long",
                        Direction::Short => factor.direction == "short",
                    }
                    && (!self.implemented_only || factor.implemented)
            })
            .collect()
    }

    fn web_link(
        label: &'static str,
        page: &'static str,
        index: usize,
        cx: &mut Context<Self>,
    ) -> impl IntoElement {
        div()
            .id(("web-nav", index))
            .w_full()
            .px_4()
            .py_3()
            .rounded_lg()
            .text_color(MUTED)
            .cursor_pointer()
            .hover(|style| style.bg(HOVER).text_color(TEXT))
            .child(h_flex().justify_between().child(label).child("↗"))
            .on_click(cx.listener(move |_, _, _, cx| {
                cx.open_url(&format!("http://127.0.0.1:4870/#{page}"));
            }))
    }

    fn stat(label: &'static str, value: usize, note: &'static str, color: Rgba) -> Div {
        v_flex()
            .flex_1()
            .min_w(px(135.))
            .p_4()
            .gap_2()
            .bg(PANEL)
            .border_1()
            .border_color(LINE)
            .rounded_lg()
            .child(div().text_xs().text_color(MUTED).child(label))
            .child(
                h_flex()
                    .items_end()
                    .gap_2()
                    .child(
                        div()
                            .text_3xl()
                            .font_bold()
                            .text_color(color)
                            .child(value.to_string()),
                    )
                    .child(div().text_xs().pb_1().text_color(MUTED).child(note)),
            )
    }

    fn filter_button(
        label: &'static str,
        filter: Direction,
        active: bool,
        index: usize,
        cx: &mut Context<Self>,
    ) -> impl IntoElement {
        div()
            .id(("direction", index))
            .px_4()
            .py_2()
            .rounded_lg()
            .cursor_pointer()
            .border_1()
            .border_color(if active { GREEN } else { LINE })
            .bg(if active { color(0x293d2d) } else { PANEL })
            .text_color(if active { GREEN } else { MUTED })
            .child(label)
            .on_click(cx.listener(move |this, _, _, cx| {
                this.direction = filter;
                this.selected = None;
                cx.notify();
            }))
    }

    fn factor_card(
        id: &str,
        factor: &Factor,
        active: bool,
        index: usize,
        cx: &mut Context<Self>,
    ) -> impl IntoElement {
        let selected = id.to_owned();
        let tint = if factor.direction == "long" {
            GREEN
        } else {
            BLUE
        };
        v_flex()
            .id(("factor", index))
            .w_full()
            .p_4()
            .gap_2()
            .rounded_lg()
            .border_1()
            .border_color(if active { GREEN } else { LINE })
            .bg(if active { color(0x1b302a) } else { PANEL })
            .cursor_pointer()
            .hover(|style| style.bg(HOVER))
            .child(
                h_flex()
                    .justify_between()
                    .items_center()
                    .child(
                        div()
                            .font_semibold()
                            .text_color(TEXT)
                            .child(factor.name.clone()),
                    )
                    .child(
                        div()
                            .text_xs()
                            .font_bold()
                            .text_color(tint)
                            .child(factor.direction.to_uppercase()),
                    ),
            )
            .child(div().text_xs().text_color(MUTED).child(id.to_owned()))
            .child(
                div()
                    .text_sm()
                    .text_color(MUTED)
                    .child(factor.description.clone()),
            )
            .child(
                h_flex()
                    .justify_between()
                    .child(
                        div()
                            .text_xs()
                            .text_color(if factor.implemented { GREEN } else { AMBER })
                            .child(if factor.implemented {
                                "● 已实现"
                            } else {
                                "● 待实现"
                            }),
                    )
                    .child(div().text_xs().text_color(MUTED).child("查看详情 →")),
            )
            .on_click(cx.listener(move |this, _, _, cx| {
                this.selected = Some(selected.clone());
                cx.notify();
            }))
    }

    fn section(title: &'static str, english: &'static str) -> Div {
        h_flex()
            .pt_3()
            .pb_3()
            .justify_between()
            .border_b_1()
            .border_color(LINE)
            .child(div().font_semibold().text_color(TEXT).child(title))
            .child(div().text_xs().text_color(MUTED).child(english))
    }

    fn tag(label: &'static str, color: Rgba) -> Div {
        div()
            .px_3()
            .py_2()
            .bg(BG)
            .rounded_lg()
            .text_xs()
            .font_bold()
            .text_color(color)
            .child(label)
    }

    fn detail(&self, id: &str, factor: &Factor, cx: &mut Context<Self>) -> Stateful<Div> {
        let launch_id = id.to_owned();
        let pair = factor
            .pair_with
            .as_ref()
            .filter(|mate| self.factors.iter().any(|(key, _)| key == *mate));
        let mut parameters = v_flex().gap_2();
        if factor.inputs.is_empty() {
            parameters = parameters.child(div().text_sm().text_color(MUTED).child("无需输入参数"));
        }
        for input in &factor.inputs {
            parameters = parameters.child(
                h_flex()
                    .justify_between()
                    .py_2()
                    .border_b_1()
                    .border_color(LINE)
                    .child(div().text_sm().text_color(MUTED).child(input.label.clone()))
                    .child(
                        div()
                            .text_sm()
                            .font_semibold()
                            .text_color(TEXT)
                            .child(input.default.to_string()),
                    ),
            );
        }
        let mut logic = v_flex().gap_2();
        if factor.setup.is_empty() && factor.condition.is_empty() {
            logic = logic.child(div().text_sm().text_color(MUTED).child("尚无信号逻辑"));
        }
        for line in &factor.setup {
            logic = logic.child(div().text_sm().text_color(BLUE).child(line.clone()));
        }
        if !factor.condition.is_empty() {
            logic = logic.child(
                div()
                    .text_sm()
                    .text_color(GREEN)
                    .child(format!("if {}", factor.condition)),
            );
        }
        v_flex()
            .id("factor-detail")
            .flex_1()
            .h_full()
            .min_w(px(370.))
            .overflow_y_scroll()
            .p_6()
            .gap_5()
            .bg(PANEL)
            .border_1()
            .border_color(LINE)
            .rounded_lg()
            .child(
                div()
                    .text_xs()
                    .font_bold()
                    .text_color(GREEN)
                    .child("FACTOR PROFILE  /  因子详情"),
            )
            .child(
                v_flex()
                    .gap_2()
                    .child(
                        div()
                            .text_3xl()
                            .font_bold()
                            .text_color(TEXT)
                            .child(factor.name.clone()),
                    )
                    .child(div().text_sm().text_color(MUTED).child(id.to_owned()))
                    .child(
                        div()
                            .pt_2()
                            .text_color(TEXT)
                            .child(factor.description.clone()),
                    ),
            )
            .child(
                h_flex()
                    .gap_3()
                    .child(Self::tag(
                        if factor.direction == "long" {
                            "↗ LONG · 做多"
                        } else {
                            "↘ SHORT · 做空"
                        },
                        if factor.direction == "long" {
                            GREEN
                        } else {
                            BLUE
                        },
                    ))
                    .child(Self::tag(
                        if factor.implemented {
                            "已实现"
                        } else {
                            "待实现"
                        },
                        if factor.implemented { GREEN } else { AMBER },
                    )),
            )
            .when(factor.implemented, |this| {
                this.child(
                    Button::new("open-backtest")
                        .label("在 Web 工作台回测 ↗")
                        .on_click(cx.listener(move |_, _, _, cx| {
                            cx.open_url(&format!(
                                "http://127.0.0.1:4870/?factor={}#backtest",
                                launch_id
                            ));
                        })),
                )
            })
            .child(Self::section("配对关系", "PAIRING"))
            .child(
                h_flex()
                    .w_full()
                    .p_4()
                    .justify_between()
                    .items_center()
                    .bg(BG)
                    .rounded_lg()
                    .child(div().text_sm().text_color(MUTED).child("配对因子"))
                    .child(
                        div()
                            .text_sm()
                            .text_color(TEXT)
                            .child(pair.map(|s| s.as_str()).unwrap_or("无配对").to_owned()),
                    ),
            )
            .when_some(pair.cloned(), |this, pair_id| {
                this.child(
                    Button::new("show-pair")
                        .label("查看配对因子 →")
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.direction = Direction::All;
                            this.implemented_only = false;
                            this.search.update(cx, |search, cx| {
                                search.set_value(pair_id.clone(), window, cx)
                            });
                            this.selected = Some(pair_id.clone());
                            cx.notify();
                        })),
                )
            })
            .child(Self::section("参数配置", "PARAMETERS"))
            .child(parameters)
            .child(Self::section("信号逻辑", "SIGNAL LOGIC"))
            .child(logic.bg(BG).p_4().rounded_lg())
            .when_some(factor.note.clone(), |this, note| {
                this.child(div().text_sm().text_color(MUTED).child(note))
            })
    }
}

impl Render for QuantLab {
    fn render(&mut self, _: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        let query = self.search.read(cx).value();
        let filtered = self.filtered(&query);
        let selected = self
            .selected
            .as_deref()
            .filter(|id| filtered.iter().any(|(key, _)| key == id))
            .or_else(|| filtered.first().map(|(id, _)| id.as_str()));
        let implemented = self.factors.iter().filter(|(_, f)| f.implemented).count();
        let pending = self.factors.len() - implemented;
        let pairs = self
            .factors
            .iter()
            .filter_map(|(id, f)| {
                f.pair_with.as_ref().map(|other| {
                    if id < other {
                        (id.clone(), other.clone())
                    } else {
                        (other.clone(), id.clone())
                    }
                })
            })
            .collect::<HashSet<_>>()
            .len();

        let mut cards = v_flex().gap_3();
        for (index, (id, factor)) in filtered.iter().enumerate() {
            cards = cards.child(Self::factor_card(
                id,
                factor,
                selected == Some(id.as_str()),
                index,
                cx,
            ));
        }
        if filtered.is_empty() {
            cards = cards.child(
                v_flex()
                    .p_6()
                    .gap_2()
                    .items_center()
                    .text_color(MUTED)
                    .child(div().text_lg().text_color(TEXT).child("没有找到匹配的因子"))
                    .child("试试其他关键词或筛选条件"),
            );
        }
        let detail = if let Some(id) = selected {
            let (_, factor) = self.factors.iter().find(|(key, _)| key == id).unwrap();
            self.detail(id, factor, cx)
        } else {
            v_flex()
                .id("empty-detail")
                .flex_1()
                .h_full()
                .p_6()
                .bg(PANEL)
                .border_1()
                .border_color(LINE)
                .rounded_lg()
                .child(div().text_color(MUTED).child("选择一个因子查看详情"))
        };

        let mut rail = v_flex()
            .w(px(228.))
            .h_full()
            .flex_shrink_0()
            .p_5()
            .gap_4()
            .bg(RAIL)
            .border_r_1()
            .border_color(LINE)
            .child(
                h_flex()
                    .gap_3()
                    .items_center()
                    .child(
                        div()
                            .w(px(34.))
                            .h(px(34.))
                            .rounded_lg()
                            .bg(GREEN)
                            .text_color(BG)
                            .font_bold()
                            .flex()
                            .items_center()
                            .justify_center()
                            .child("Q"),
                    )
                    .child(
                        v_flex()
                            .child(div().font_bold().text_color(TEXT).child("QUANT LAB"))
                            .child(div().text_xs().text_color(MUTED).child("RESEARCH DESKTOP")),
                    ),
            )
            .child(
                div()
                    .pt_5()
                    .text_xs()
                    .font_bold()
                    .text_color(MUTED)
                    .child("WORKSPACE"),
            )
            .child(
                div()
                    .px_4()
                    .py_3()
                    .rounded_lg()
                    .bg(color(0x263d31))
                    .text_color(GREEN)
                    .font_semibold()
                    .child("▦  因子库"),
            )
            .child(
                div()
                    .pt_3()
                    .text_xs()
                    .font_bold()
                    .text_color(MUTED)
                    .child("WEB 工作台  ↗"),
            );
        for (index, (label, page)) in [
            ("回测分析", "backtest"),
            ("模拟交易", "paper"),
            ("实盘查询", "live"),
            ("公司分析", "company"),
            ("交易记录", "trade-review"),
        ]
        .into_iter()
        .enumerate()
        {
            rail = rail.child(Self::web_link(label, page, index, cx));
        }
        rail = rail.child(
            v_flex()
                .mt_auto()
                .p_4()
                .gap_2()
                .bg(PANEL)
                .rounded_lg()
                .child(
                    div()
                        .text_xs()
                        .font_bold()
                        .text_color(GREEN)
                        .child("● 本地因子库"),
                )
                .child(
                    div()
                        .text_xs()
                        .text_color(MUTED)
                        .child("数据来自 strategy_kit.json"),
                )
                .child(
                    div()
                        .text_xs()
                        .text_color(MUTED)
                        .child("Web 功能需先启动 cargo run"),
                ),
        );

        let main = v_flex()
            .flex_1()
            .h_full()
            .min_w(px(760.))
            .p_6()
            .gap_5()
            .bg(BG)
            .child(
                h_flex()
                    .justify_between()
                    .items_start()
                    .child(
                        v_flex()
                            .gap_2()
                            .child(
                                div()
                                    .text_xs()
                                    .font_bold()
                                    .text_color(GREEN)
                                    .child("RESEARCH  /  FACTOR LIBRARY"),
                            )
                            .child(
                                div()
                                    .text_3xl()
                                    .font_bold()
                                    .text_color(TEXT)
                                    .child("因子实验室"),
                            )
                            .child(
                                div()
                                    .text_sm()
                                    .text_color(MUTED)
                                    .child("浏览信号、检查参数与逻辑，快速找到下一次回测的起点。"),
                            ),
                    )
                    .child(
                        Button::new("refresh")
                            .label("↻  刷新因子库")
                            .on_click(cx.listener(|this, _, _, cx| {
                                this.reload();
                                cx.notify();
                            })),
                    ),
            )
            .when_some(self.error.clone(), |this, error| {
                this.child(
                    div()
                        .p_3()
                        .bg(color(0x422a2a))
                        .text_color(color(0xffb7b7))
                        .rounded_lg()
                        .child(error),
                )
            })
            .child(
                h_flex()
                    .gap_3()
                    .child(Self::stat("因子总数", self.factors.len(), "个信号", TEXT))
                    .child(Self::stat("已实现", implemented, "可用于策略", GREEN))
                    .child(Self::stat("配对组合", pairs, "组多空", BLUE))
                    .child(Self::stat("待实现", pending, "待完善", AMBER)),
            )
            .child(
                h_flex()
                    .flex_1()
                    .min_h(px(0.))
                    .gap_4()
                    .child(
                        v_flex()
                            .w(px(380.))
                            .h_full()
                            .flex_shrink_0()
                            .gap_3()
                            .child(
                                h_flex()
                                    .justify_between()
                                    .items_center()
                                    .child(
                                        div()
                                            .text_lg()
                                            .font_semibold()
                                            .text_color(TEXT)
                                            .child("全部因子"),
                                    )
                                    .child(
                                        div()
                                            .text_sm()
                                            .text_color(MUTED)
                                            .child(format!("{} 个结果", filtered.len())),
                                    ),
                            )
                            .child(Input::new(&self.search).id("factor-search").w_full())
                            .child(
                                h_flex()
                                    .gap_2()
                                    .child(Self::filter_button(
                                        "全部",
                                        Direction::All,
                                        self.direction == Direction::All,
                                        0,
                                        cx,
                                    ))
                                    .child(Self::filter_button(
                                        "做多",
                                        Direction::Long,
                                        self.direction == Direction::Long,
                                        1,
                                        cx,
                                    ))
                                    .child(Self::filter_button(
                                        "做空",
                                        Direction::Short,
                                        self.direction == Direction::Short,
                                        2,
                                        cx,
                                    )),
                            )
                            .child(
                                div()
                                    .id("implemented-filter")
                                    .cursor_pointer()
                                    .text_sm()
                                    .text_color(if self.implemented_only { GREEN } else { MUTED })
                                    .child(if self.implemented_only {
                                        "☑  仅看已实现"
                                    } else {
                                        "☐  仅看已实现"
                                    })
                                    .on_click(cx.listener(|this, _, _, cx| {
                                        this.implemented_only = !this.implemented_only;
                                        this.selected = None;
                                        cx.notify();
                                    })),
                            )
                            .child(
                                div()
                                    .id("factor-list")
                                    .flex_1()
                                    .overflow_y_scroll()
                                    .child(cards),
                            ),
                    )
                    .child(detail),
            );
        h_flex()
            .size_full()
            .bg(BG)
            .text_color(TEXT)
            .child(rail)
            .child(main)
    }
}

fn main() {
    gpui_kit::application().run(|cx| {
        gpui_kit::init(cx);
        Theme::change(ThemeMode::Dark, None, cx);
        let bounds = Bounds {
            origin: point(px(36.), px(32.)),
            size: size(px(1320.), px(820.)),
        };
        let options = WindowOptions {
            window_bounds: Some(WindowBounds::Windowed(bounds)),
            ..Default::default()
        };
        gpui_kit::open_window(options, cx, |window, cx| {
            cx.new(|cx| QuantLab::new(window, cx))
        })
        .expect("无法打开 Quant Lab 桌面窗口");
        cx.activate(true);
    });
}
