mod embedded;
#[path = "../../src/factors.rs"]
mod factors;

use embedded::EmbeddedPages;
use factors::{Factor, FactorLib};
use gpui_kit::component::button::Button;
use gpui_kit::component::input::{Input, InputEvent, InputState};
use gpui_kit::component::*;
use gpui_kit::prelude::FluentBuilder;
use gpui_kit::*;
use std::collections::HashSet;
use std::path::PathBuf;

actions!(quant_lab, [Quit]);

fn quit(_: &Quit, cx: &mut App) {
    cx.quit();
}

const fn color(hex: u32) -> Rgba {
    Rgba {
        r: ((hex >> 16) & 0xff) as f32 / 255.0,
        g: ((hex >> 8) & 0xff) as f32 / 255.0,
        b: (hex & 0xff) as f32 / 255.0,
        a: 1.0,
    }
}

const BG: Rgba = color(0x10151c);
const RAIL: Rgba = color(0x131a22);
const PANEL: Rgba = color(0x171e27);
const HOVER: Rgba = color(0x202a35);
const LINE: Rgba = color(0x2b3542);
const TEXT: Rgba = color(0xe6edf3);
const MUTED: Rgba = color(0x9caabc);
const GREEN: Rgba = color(0xb8de8f);
const BLUE: Rgba = color(0x84b6d9);
const AMBER: Rgba = color(0xdcb67b);

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
    active_page: &'static str,
    navigation_error: Option<String>,
    embedded: EmbeddedPages,
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
            active_page: "factors",
            navigation_error: None,
            embedded: EmbeddedPages::new(),
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

    fn navigate(
        &mut self,
        page: &'static str,
        factor: Option<&str>,
        window: &mut Window,
        cx: &mut Context<Self>,
    ) {
        self.active_page = page;
        self.navigation_error = if page == "factors" {
            self.embedded.hide().err().map(|error| error.to_string())
        } else {
            let view = cx.entity().downgrade();
            let async_cx = cx.to_async();
            self.embedded
                .open(page, factor, window, move |route| {
                    let page = match route {
                        "company" => "company", "daily-close" => "daily-close",
                        "research" => "research", "backtest" => "backtest",
                        "paper" => "paper", "live" => "live",
                        "trade-review" => "trade-review", "settings" => "settings",
                        "factors" => "factors", _ => return,
                    };
                    async_cx.update(|cx| {
                        let _ = view.update(cx, |this, cx| {
                            if this.active_page != page {
                                this.active_page = page;
                                if page == "factors" { let _ = this.embedded.hide(); }
                                cx.notify();
                            }
                        });
                    });
                })
                .err()
                .map(|error| error.to_string())
        };
        cx.notify();
    }

    fn page_link(
        label: &'static str,
        page: &'static str,
        index: usize,
        active: bool,
        cx: &mut Context<Self>,
    ) -> impl IntoElement {
        div()
            .id(("page-nav", index))
            .w_full()
            .px_3()
            .h(px(38.))
            .flex()
            .items_center()
            .text_size(px(13.))
            .rounded_md()
            .bg(if active { color(0x25332b) } else { RAIL })
            .text_color(if active { GREEN } else { MUTED })
            .cursor_pointer()
            .hover(|style| style.bg(HOVER).text_color(TEXT))
            .child(label)
            .on_click(cx.listener(move |this, _, window, cx| {
                this.navigate(page, None, window, cx);
            }))
    }

    fn stat(label: &'static str, value: usize, note: &'static str, color: Rgba) -> Div {
        v_flex()
            .flex_1()
            .min_w(px(100.))
            .p_3()
            .gap_1()
            .bg(PANEL)
            .border_1()
            .border_color(LINE)
            .rounded_md()
            .child(div().text_xs().text_color(MUTED).child(label))
            .child(
                h_flex()
                    .items_end()
                    .gap_1()
                    .child(
                        div()
                            .text_size(px(22.))
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
            .p_3()
            .gap_1()
            .rounded_md()
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
                h_flex()
                    .justify_between()
                    .child(
                        div()
                            .text_xs()
                            .text_color(if factor.implemented { GREEN } else { AMBER })
                            .child(if factor.implemented {
                                "已实现"
                            } else {
                                "待实现"
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
            .min_w(px(280.))
            .overflow_y_scroll()
            .p_5()
            .gap_3()
            .bg(PANEL)
            .border_1()
            .border_color(LINE)
            .rounded_lg()
            .child(
                div()
                    .text_xs()
                    .font_bold()
                    .text_color(GREEN)
                    .child("因子详情"),
            )
            .child(
                v_flex()
                    .gap_2()
                    .child(
                        div()
                            .text_size(px(23.))
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
                            "LONG · 做多"
                        } else {
                            "SHORT · 做空"
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
                        .label("运行策略回测 →")
                        .on_click(cx.listener(move |this, _, window, cx| {
                            this.navigate("backtest", Some(&launch_id), window, cx);
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
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if self.active_page != "factors" {
            if let Err(error) = self.embedded.resize(window) {
                self.navigation_error = Some(error.to_string());
            }
        }
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
            .w(px(204.))
            .h_full()
            .flex_shrink_0()
            .px_3()
            .py_5()
            .gap_1()
            .bg(RAIL)
            .border_r_1()
            .border_color(LINE)
            .child(
                h_flex()
                    .px_2()
                    .pb_5()
                    .gap_3()
                    .items_center()
                    .child(
                        div()
                            .w(px(28.))
                            .h(px(28.))
                            .rounded_md()
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
                            .child(
                                div()
                                    .text_size(px(14.))
                                    .font_bold()
                                    .text_color(TEXT)
                                    .child("Quant Lab"),
                            )
                            .child(
                                div()
                                    .text_size(px(10.))
                                    .text_color(MUTED)
                                    .child("量化研究工作台"),
                            ),
                    ),
            );
        let mut nav_index = 0;
        for (group, links) in [
            (
                "研究工作区",
                vec![
                    ("公司分析", "company"),
                    ("每日收盘", "daily-close"),
                    ("研究档案", "research"),
                    ("因子实验室", "factors"),
                    ("策略回测", "backtest"),
                ],
            ),
            (
                "交易跟踪",
                vec![
                    ("模拟交易", "paper"),
                    ("实盘账户", "live"),
                    ("交易复盘", "trade-review"),
                ],
            ),
        ] {
            rail = rail.child(
                div()
                    .px_3()
                    .pt_4()
                    .pb_2()
                    .text_size(px(10.))
                    .text_color(MUTED)
                    .child(group),
            );
            for (label, page) in links {
                rail = rail.child(Self::page_link(
                    label,
                    page,
                    nav_index,
                    self.active_page == page,
                    cx,
                ));
                nav_index += 1;
            }
        }
        rail = rail.child(
            v_flex()
                .mt_auto()
                .pt_4()
                .border_t_1()
                .border_color(LINE)
                .gap_3()
                .child(Self::page_link(
                    "连接设置",
                    "settings",
                    nav_index,
                    self.active_page == "settings",
                    cx,
                ))
                .child(
                    h_flex()
                        .px_3()
                        .gap_2()
                        .child(div().text_size(px(10.)).text_color(GREEN).child("•"))
                        .child(
                            div()
                                .text_size(px(10.))
                                .text_color(MUTED)
                                .child("本地工作区"),
                        ),
                ),
        );

        let main = v_flex()
            .flex_1()
            .h_full()
            .min_w(px(640.))
            .p_5()
            .gap_4()
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
                                    .child("研究工作区 / 信号库"),
                            )
                            .child(
                                div()
                                    .text_size(px(23.))
                                    .font_bold()
                                    .text_color(TEXT)
                                    .child("因子实验室"),
                            )
                            .child(
                                div()
                                    .text_sm()
                                    .text_color(MUTED)
                                    .child("浏览策略信号，检查参数与逻辑，选择因子进入回测。"),
                            ),
                    )
                    .child(
                        Button::new("refresh")
                            .label("刷新因子库")
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
                            .w(px(300.))
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
                                        "✓  仅看已实现"
                                    } else {
                                        "仅看已实现"
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
        let content = if self.active_page == "factors" {
            main
        } else {
            v_flex().flex_1().h_full().p_6().bg(BG).when_some(
                self.navigation_error.clone(),
                |this, error| {
                    this.child(
                        v_flex()
                            .p_5()
                            .gap_3()
                            .bg(PANEL)
                            .rounded_lg()
                            .child(div().text_lg().font_semibold().child("内置页面无法打开"))
                            .child(div().text_color(MUTED).child(error)),
                    )
                },
            )
        };
        h_flex()
            .size_full()
            .text_size(px(13.))
            .bg(BG)
            .text_color(TEXT)
            .child(rail)
            .child(content)
    }
}

fn main() {
    gpui_kit::application().run(|cx| {
        gpui_kit::init(cx);
        cx.on_action(quit);
        cx.bind_keys([KeyBinding::new("cmd-q", Quit, None)]);
        cx.set_menus([
            Menu::new("Quant Lab").items([MenuItem::action("退出 Quant Lab", Quit)]),
            Menu::new("编辑").items([
                MenuItem::os_action("剪切", gpui_kit::component::input::Cut, OsAction::Cut),
                MenuItem::os_action("复制", gpui_kit::component::input::Copy, OsAction::Copy),
                MenuItem::os_action("粘贴", gpui_kit::component::input::Paste, OsAction::Paste),
                MenuItem::os_action(
                    "全选",
                    gpui_kit::component::input::SelectAll,
                    OsAction::SelectAll,
                ),
            ]),
        ]);
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
