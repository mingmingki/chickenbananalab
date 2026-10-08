import logging
import os
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import config
import gemini_analyzer
import okx_client
import pnl_store
import capital_flow
import timeframes
import trade_log
import trader
from okx_client import OkxClient
from state import TraderState

GREEN = "#2e9e44"
RED = "#c0392b"
AMBER = "#b58900"

# 데스크톱 앱은 항상 1명(로컬 사용자)이라 계정 디렉터리 없이 프로젝트 루트의 .env를 그대로 쓴다.
_cfg = config.UserConfig(config.PROJECT_DIR)
_cfg.logger = logging.getLogger("trader")
_state = TraderState()


class LogTextHandler(logging.Handler):
    def __init__(self, widget):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record)

        def append():
            self.widget.configure(state="normal")
            self.widget.insert("end", msg + "\n")
            self.widget.see("end")
            self.widget.configure(state="disabled")

        try:
            self.widget.after(0, append)
        except Exception:
            pass


class KeyRow:
    """API 키 입력 한 줄: 라벨 + 입력창 + 빨강/초록 신호점."""

    def __init__(self, parent, row: int, label: str, initial_value: str, on_check=None, mask: bool = True):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
        self.var = tk.StringVar(value=initial_value)
        entry = ttk.Entry(parent, textvariable=self.var, width=38, show="•" if mask else "")
        entry.grid(row=row, column=1, sticky="we", pady=3)
        self.dot = ttk.Label(parent, text="●", foreground=RED)
        self.dot.grid(row=row, column=2, padx=(8, 4))
        if on_check is not None:
            ttk.Button(parent, text="확인", command=on_check, width=6).grid(row=row, column=3, pady=3)

    def value(self) -> str:
        return self.var.get().strip()

    def set_ready(self, ready: bool):
        self.dot.configure(foreground=GREEN if ready else RED)

    def set_checking(self):
        self.dot.configure(foreground=AMBER)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("자동매매 (OKX x Gemini)")
        root.geometry("780x920")
        root.minsize(640, 700)

        self.stop_event = None
        self.thread = None
        self.gemini_validated = False
        self.okx_validated = False

        # --- 창 전체를 세로 스크롤 가능하게 감싸는 캔버스 ---
        outer = ttk.Frame(root)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        content = ttk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def _sync_scrollregion(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_content_width(event):
            canvas.itemconfigure(content_window, width=event.width)

        content.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_content_width)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * event.delta), "units")

        canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # --- API 키 ---
        key_frame = ttk.LabelFrame(content, text="API 키", padding=12)
        key_frame.pack(fill="x", padx=12, pady=(12, 6))
        key_frame.columnconfigure(1, weight=1)

        # 이전에 저장된 키가 남아있어 헷갈리지 않도록, 실행할 때마다 칸은 항상 비워서 보여준다
        # (실제 .env 값은 그대로 유지되며 지워지지 않음 — 화면 표시만 비움).
        self.gemini_row = KeyRow(key_frame, 0, "Gemini API 키", "", on_check=self.check_gemini_key)
        self.okx_key_row = KeyRow(key_frame, 1, "OKX API 키", "", on_check=self.check_okx_keys)
        self.okx_secret_row = KeyRow(key_frame, 2, "OKX Secret", "")
        self.okx_pass_row = KeyRow(key_frame, 3, "OKX Passphrase", "")

        self.key_hint = ttk.Label(key_frame, text="", foreground=RED, wraplength=560, justify="left")
        self.key_hint.grid(row=4, column=0, columnspan=4, sticky="w", pady=(8, 0))

        # --- 매매 설정 ---
        settings_frame = ttk.LabelFrame(content, text="매매 설정", padding=12)
        settings_frame.pack(fill="x", padx=12, pady=6)

        ttk.Label(settings_frame, text="검토 주기(초)").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.poll_var = tk.StringVar(value=str(_cfg.POLL_INTERVAL_SECONDS))
        ttk.Entry(settings_frame, textvariable=self.poll_var, width=10).grid(row=0, column=1, sticky="w")
        self.poll_var.trace_add("write", lambda *_: self._update_timeframe_preview())

        ttk.Label(settings_frame, text="레버리지(x)").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(6, 0))
        self.leverage_var = tk.StringVar(value=str(_cfg.LEVERAGE))
        ttk.Entry(settings_frame, textvariable=self.leverage_var, width=10).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        self.timeframe_preview = ttk.Label(settings_frame, foreground="#666666")
        self.timeframe_preview.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))

        ttk.Label(settings_frame, text=f"심볼: {', '.join(_cfg.SYMBOLS)}").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        ttk.Button(settings_frame, text="설정 저장", command=self.save_settings).grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        self.save_hint = ttk.Label(settings_frame, text="")
        self.save_hint.grid(row=4, column=1, columnspan=2, sticky="w", pady=(10, 0))

        # --- 포지션 크기 ---
        size_frame = ttk.LabelFrame(content, text="포지션 크기", padding=12)
        size_frame.pack(fill="x", padx=12, pady=6)

        self.size_mode_var = tk.StringVar(value=_cfg.POSITION_SIZE_MODE)

        ttk.Radiobutton(
            size_frame,
            text="스탑로스 기준 자동계산 (RISK_PER_TRADE_PCT)",
            variable=self.size_mode_var,
            value="RISK",
            command=self._update_size_mode_state,
        ).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Radiobutton(
            size_frame, text="고정 금액", variable=self.size_mode_var, value="FIXED",
            command=self._update_size_mode_state,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.fixed_amount_var = tk.StringVar(value=str(_cfg.POSITION_FIXED_USDT))
        self.fixed_amount_entry = ttk.Entry(size_frame, textvariable=self.fixed_amount_var, width=10)
        self.fixed_amount_entry.grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(size_frame, text="USDT (증거금 기준)").grid(row=1, column=2, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Radiobutton(
            size_frame, text="자산 비율", variable=self.size_mode_var, value="PERCENT",
            command=self._update_size_mode_state,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.percent_var = tk.StringVar(value=str(_cfg.POSITION_PERCENT))
        self.percent_combo = ttk.Combobox(
            size_frame, textvariable=self.percent_var, width=8,
            values=["5", "10", "20", "30", "50", "75", "100"],
        )
        self.percent_combo.grid(row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(size_frame, text="% (계좌 자산 대비 증거금 비율)").grid(
            row=2, column=2, sticky="w", padx=(8, 0), pady=(6, 0)
        )

        ttk.Label(
            size_frame,
            text="※ 고정 금액/자산 비율은 증거금 기준이며, 실제 포지션 크기는 여기에 레버리지를 곱한 값입니다\n"
            "   (예: 10 USDT × 3배 레버리지 = 30 USDT 포지션).",
            foreground="#666666",
            justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        self._update_size_mode_state()

        # --- 계좌 / 총 수익 ---
        profit_frame = ttk.LabelFrame(content, text="계좌 / 총 수익", padding=12)
        profit_frame.pack(fill="x", padx=12, pady=6)

        self.equity_var = tk.StringVar(value="자산: - (조회 필요)")
        ttk.Label(profit_frame, textvariable=self.equity_var, font=("SF Pro Text", 13, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(profit_frame, text="자산 조회", command=self.check_balance).grid(row=0, column=1, padx=(12, 0))

        self.profit_var = tk.StringVar(value="매매기여 총수익: 시작 후 자금흐름 보정으로 계산됩니다")
        self.profit_label = ttk.Label(profit_frame, textvariable=self.profit_var, font=("SF Pro Text", 12))
        self.profit_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(profit_frame, text="수익 기준 초기화", command=self.reset_baseline).grid(
            row=1, column=1, padx=(12, 0), pady=(4, 0)
        )
        self._update_timeframe_preview()

        # --- 상태 ---
        status = ttk.Frame(content, padding=(12, 0))
        status.pack(fill="x")
        self.status_var = tk.StringVar(value="상태: 중지됨")
        ttk.Label(status, textvariable=self.status_var, font=("SF Pro Text", 13, "bold")).pack(anchor="w")

        # --- 거래할 종목 선택 ---
        symbol_select_frame = ttk.LabelFrame(content, text="거래할 종목 선택", padding=12)
        symbol_select_frame.pack(fill="x", padx=12, pady=6)

        self.symbol_enabled_vars: dict[str, tk.BooleanVar] = {}
        self.symbol_checkbuttons: dict[str, ttk.Checkbutton] = {}
        for i, symbol in enumerate(_cfg.SYMBOLS):
            var = tk.BooleanVar(value=(symbol in _cfg.ENABLED_SYMBOLS))
            self.symbol_enabled_vars[symbol] = var
            cb = ttk.Checkbutton(
                symbol_select_frame, text=symbol, variable=var, command=self._on_symbol_toggle
            )
            cb.grid(row=0, column=i, sticky="w", padx=(0, 20))
            self.symbol_checkbuttons[symbol] = cb
        ttk.Label(
            symbol_select_frame,
            text="※ 실행 중 변경은 다음 시작부터 적용됩니다",
            foreground="#666666",
        ).grid(row=1, column=0, columnspan=max(len(_cfg.SYMBOLS), 1), sticky="w", pady=(6, 0))

        # --- 심볼별 현황 ---
        table_frame = ttk.Frame(content, padding=12)
        table_frame.pack(fill="x")
        columns = ("position", "action", "confidence", "reasoning")
        self.tree = ttk.Treeview(
            table_frame, columns=columns, show="tree headings", height=len(_cfg.SYMBOLS)
        )
        self.tree.heading("#0", text="심볼")
        self.tree.heading("position", text="포지션")
        self.tree.heading("action", text="최근 판단")
        self.tree.heading("confidence", text="확신도")
        self.tree.heading("reasoning", text="근거")
        self.tree.column("#0", width=120, anchor="w")
        self.tree.column("position", width=170, anchor="w")
        self.tree.column("action", width=70, anchor="center")
        self.tree.column("confidence", width=60, anchor="center")
        self.tree.column("reasoning", width=280, anchor="w")
        self.tree.tag_configure("disabled_symbol", foreground="#999999")
        self.tree.pack(fill="x")
        for symbol in _cfg.SYMBOLS:
            self.tree.insert("", "end", iid=symbol, text=symbol, values=("-", "-", "-", "-"))

        # --- 거래 기록 ---
        record_frame = ttk.LabelFrame(content, text="거래 기록", padding=12)
        record_frame.pack(fill="x", padx=12, pady=6)

        record_header = ttk.Frame(record_frame)
        record_header.pack(fill="x")
        self.record_var = tk.StringVar(value="아직 청산된 거래가 없습니다")
        ttk.Label(record_header, textvariable=self.record_var).pack(side="left")
        ttk.Button(record_header, text="기록 파일 열기", command=self.open_trade_log).pack(side="right")

        record_list_frame = ttk.Frame(record_frame)
        record_list_frame.pack(fill="both", expand=True, pady=(8, 0))
        record_columns = ("time", "symbol", "side", "pnl", "reason")
        self.record_tree = ttk.Treeview(
            record_list_frame, columns=record_columns, show="headings", height=8
        )
        self.record_tree.heading("time", text="시각")
        self.record_tree.heading("symbol", text="심볼")
        self.record_tree.heading("side", text="방향")
        self.record_tree.heading("pnl", text="손익(USDT)")
        self.record_tree.heading("reason", text="사유")
        self.record_tree.column("time", width=130, anchor="w")
        self.record_tree.column("symbol", width=120, anchor="w")
        self.record_tree.column("side", width=60, anchor="center")
        self.record_tree.column("pnl", width=90, anchor="e")
        self.record_tree.column("reason", width=140, anchor="w")

        record_vscroll = ttk.Scrollbar(record_list_frame, orient="vertical", command=self.record_tree.yview)
        self.record_tree.configure(yscrollcommand=record_vscroll.set)
        self.record_tree.pack(side="left", fill="both", expand=True)
        record_vscroll.pack(side="right", fill="y")

        # --- 버튼 ---
        btn_frame = ttk.Frame(content, padding=12)
        btn_frame.pack(fill="x")
        self.start_btn = ttk.Button(btn_frame, text="시작", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frame, text="중단 (전 종목 포지션 청산)", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=6)
        ttk.Button(btn_frame, text="로그 폴더 열기", command=self.open_logs).pack(side="left", padx=6)
        ttk.Button(btn_frame, text=".env 열기", command=self.open_env).pack(side="left")

        self.dry_run_label = ttk.Label(content, padding=(12, 0), foreground="#666666")
        self.dry_run_label.pack(fill="x")

        # --- 로그 ---
        log_frame = ttk.Frame(content, padding=12)
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="실행 로그").pack(anchor="w")
        self.log_widget = scrolledtext.ScrolledText(
            log_frame, state="disabled", height=12, font=("Menlo", 11)
        )
        self.log_widget.pack(fill="both", expand=True, pady=(4, 0))

        handler = LogTextHandler(self.log_widget)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._update_start_enabled()
        self._load_persisted_baseline()
        self.refresh()

    # --- 키 확인 (칸마다 개별 확인 버튼) ---

    def _keys_ready(self) -> bool:
        return self.gemini_validated and self.okx_validated

    def _update_start_enabled(self):
        ready = self._keys_ready() and not _state.snapshot()["running"]
        self.start_btn.config(state="normal" if ready else "disabled")

    def _on_symbol_toggle(self):
        enabled = [sym for sym, var in self.symbol_enabled_vars.items() if var.get()]
        _cfg.save_env({"ENABLED_SYMBOLS": ",".join(enabled)})

    def check_gemini_key(self):
        value = self.gemini_row.value()
        if not value:
            messagebox.showerror("입력 오류", "Gemini API 키를 입력하세요.")
            return
        _cfg.save_env({"GEMINI_API_KEY": value})
        gemini_analyzer.reset_client(_cfg)
        self.gemini_row.set_checking()
        self.key_hint.config(text="Gemini 키 확인 중... (최대 1분 정도 걸릴 수 있습니다)", foreground=AMBER)
        threading.Thread(target=self._check_gemini_thread, args=(value,), daemon=True).start()

    def _check_gemini_thread(self, value):
        ok, err = gemini_analyzer.validate_key(value)
        self.root.after(0, lambda: self._apply_gemini_result(ok, err))

    def _apply_gemini_result(self, ok, err):
        self.gemini_validated = ok
        self.gemini_row.set_ready(ok)
        if ok:
            self._update_key_status_message()
        else:
            self.key_hint.config(
                text=f"Gemini 키가 맞지 않습니다. 다시 입력하세요 — {err[:120]}", foreground=RED
            )
        self._update_start_enabled()

    def check_okx_keys(self):
        api_key = self.okx_key_row.value()
        secret = self.okx_secret_row.value()
        passphrase = self.okx_pass_row.value()
        if not (api_key and secret and passphrase):
            messagebox.showerror("입력 오류", "OKX API 키, Secret, Passphrase를 모두 입력하세요.")
            return
        _cfg.save_env(
            {
                "OKX_API_KEY": api_key,
                "OKX_API_SECRET": secret,
                "OKX_API_PASSPHRASE": passphrase,
            }
        )
        self.okx_key_row.set_checking()
        self.okx_secret_row.set_checking()
        self.okx_pass_row.set_checking()
        self.key_hint.config(text="OKX 키 확인 중...", foreground=AMBER)
        threading.Thread(target=self._check_okx_thread, args=(api_key, secret, passphrase), daemon=True).start()

    def _check_okx_thread(self, api_key, secret, passphrase):
        ok, err = okx_client.validate_credentials(api_key, secret, passphrase)
        self.root.after(0, lambda: self._apply_okx_result(ok, err))

    def _apply_okx_result(self, ok, err):
        self.okx_validated = ok
        self.okx_key_row.set_ready(ok)
        self.okx_secret_row.set_ready(ok)
        self.okx_pass_row.set_ready(ok)
        if ok:
            self._update_key_status_message()
        else:
            self.key_hint.config(
                text=f"OKX 키/Secret/Passphrase가 맞지 않습니다. 다시 입력하세요 — {err[:120]}",
                foreground=RED,
            )
        self._update_start_enabled()

    def _update_key_status_message(self):
        if self.gemini_validated and self.okx_validated:
            self.key_hint.config(text="설정 완료 — 이제 자동매매를 시작할 수 있습니다", foreground=GREEN)
        else:
            self.key_hint.config(text="")

    def _load_persisted_baseline(self):
        baseline = pnl_store.load_baseline(_cfg.user_dir)
        if baseline is not None:
            _state.update(baseline_equity=baseline)

    def _update_timeframe_preview(self):
        try:
            poll_seconds = int(self.poll_var.get().strip())
        except ValueError:
            self.timeframe_preview.config(text="검토 대상 캔들: (검토 주기를 숫자로 입력하세요)")
            return
        desc = timeframes.describe(poll_seconds)
        self.timeframe_preview.config(text=f"검토 대상 캔들: {desc}")

    def _update_size_mode_state(self):
        mode = self.size_mode_var.get()
        self.fixed_amount_entry.config(state="normal" if mode == "FIXED" else "disabled")
        self.percent_combo.config(state="normal" if mode == "PERCENT" else "disabled")

    def save_settings(self):
        try:
            poll_seconds = int(self.poll_var.get().strip())
            leverage = int(self.leverage_var.get().strip())
            if poll_seconds <= 0 or leverage <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("입력 오류", "검토 주기와 레버리지는 1 이상의 숫자로 입력하세요.")
            return

        size_mode = self.size_mode_var.get()
        fixed_amount = _cfg.POSITION_FIXED_USDT
        percent = _cfg.POSITION_PERCENT

        if size_mode == "FIXED":
            try:
                fixed_amount = float(self.fixed_amount_var.get().strip())
                if fixed_amount <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("입력 오류", "고정 금액은 0보다 큰 숫자(USDT)로 입력하세요.")
                return
        elif size_mode == "PERCENT":
            try:
                percent = float(self.percent_var.get().strip())
                if not (0 < percent <= 100):
                    raise ValueError
            except ValueError:
                messagebox.showerror("입력 오류", "자산 비율은 0~100 사이 숫자로 입력하세요.")
                return

        updates = {
            "POLL_INTERVAL_SECONDS": str(poll_seconds),
            "LEVERAGE": str(leverage),
            "MAX_LEVERAGE": str(max(_cfg.MAX_LEVERAGE, leverage)),
            "POSITION_SIZE_MODE": size_mode,
            "POSITION_FIXED_USDT": str(fixed_amount),
            "POSITION_PERCENT": str(percent),
        }
        _cfg.save_env(updates)

        self.save_hint.config(text="저장됨", foreground=GREEN)
        self.root.after(2000, lambda: self.save_hint.config(text=""))

    # --- 시작 / 중단 ---

    def start(self):
        try:
            _cfg.validate()
        except RuntimeError as exc:
            messagebox.showerror("설정 오류", str(exc))
            return

        ok = messagebox.askokcancel(
            "실거래 확인",
            f"실제 자금으로 {', '.join(_cfg.SYMBOLS)} 자동매매를 시작합니다. 계속할까요?",
        )
        if not ok:
            return

        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=trader.run_all, args=(_cfg, _state, self.stop_event), daemon=True
        )
        self.thread.start()

    def stop(self):
        if self.stop_event:
            self.stop_event.set()
        clients = _state.snapshot().get("clients", {})
        self.stop_btn.config(state="disabled")
        for symbol, client in clients.items():
            threading.Thread(target=self._close_position_on_stop, args=(client, symbol), daemon=True).start()

    def _close_position_on_stop(self, client, symbol):
        try:
            trader.close_position_now(_cfg, _state, client, symbol)
        except Exception as exc:
            logging.getLogger("trader").exception("[%s] 중단 시 포지션 청산 실패", symbol)
            _state.update(last_error=f"{symbol}: {exc}")

    def check_balance(self):
        if not (_cfg.OKX_API_KEY and _cfg.OKX_API_SECRET and _cfg.OKX_API_PASSPHRASE):
            messagebox.showerror("설정 오류", "OKX API 키를 먼저 입력하고 저장하세요.")
            return
        threading.Thread(target=self._check_balance_thread, daemon=True).start()

    def _check_balance_thread(self):
        try:
            client = OkxClient(_cfg.SYMBOLS[0], _cfg)
            equity = client.fetch_usdt_equity()
            baseline = _state.snapshot().get("baseline_equity")
            if baseline is None:
                baseline = pnl_store.load_baseline(_cfg.user_dir)
                if baseline is not None:
                    _state.update(baseline_equity=baseline)
            profit = trader._cashflow_profit_snapshot(
                _cfg, equity, baseline, exchange=client.exchange, refresh=True,
            )
            _state.update(
                equity=equity,
                total_profit=profit["adjusted_profit"],
                total_profit_pct=profit["adjusted_pct"],
                raw_total_profit=profit["raw_profit"],
                raw_total_profit_pct=profit["raw_pct"],
                capital_flow_summary=profit["summary"],
                last_error=None,
            )
        except Exception as exc:
            logging.getLogger("trader").exception("자산 조회 실패")
            _state.update(last_error=str(exc))

    def reset_baseline(self):
        equity = _state.snapshot().get("equity")
        if equity is None:
            messagebox.showinfo("알림", "먼저 시작해서 자산 정보를 불러온 뒤 초기화할 수 있습니다.")
            return
        if not messagebox.askyesno(
            "수익 기준 초기화", f"현재 자산({equity:.2f} USDT)을 새 기준으로 설정할까요?"
        ):
            return
        pnl_store.save_baseline(_cfg.user_dir, equity)
        baseline_meta = pnl_store.load_baseline_metadata(_cfg.user_dir)
        capital_flow.reset_context(
            _cfg.user_dir, baseline_equity=equity, baseline_meta=baseline_meta,
        )
        zero_summary = capital_flow.compute_summary(
            baseline_equity=equity, baseline_meta=baseline_meta,
            current_equity=equity, events=[], observation_status="KNOWN",
        )
        _state.update(
            baseline_equity=equity, total_profit=0.0, total_profit_pct=0.0,
            raw_total_profit=0.0, raw_total_profit_pct=0.0,
            capital_flow_summary=zero_summary,
        )

    # --- 기타 ---

    def open_logs(self):
        subprocess.run(["open", os.path.join(config.PROJECT_DIR, "logs")])

    def open_env(self):
        subprocess.run(["open", "-a", "TextEdit", _cfg.env_path])

    def open_trade_log(self):
        log_path = os.path.join(_cfg.user_dir, "trades_log.jsonl")
        if not os.path.exists(log_path):
            messagebox.showinfo("알림", "아직 기록된 거래가 없습니다.")
            return
        subprocess.run(["open", "-a", "TextEdit", log_path])

    def on_close(self):
        if self.stop_event:
            self.stop_event.set()
        self.root.after(300, self.root.destroy)

    def refresh(self):
        snap = _state.snapshot()
        running = snap["running"]

        status = "실행 중" if running else "중지됨"
        self.status_var.set(f"상태: {status}")

        profit = snap.get("total_profit")
        profit_pct = snap.get("total_profit_pct")
        baseline = snap.get("baseline_equity")
        equity = snap.get("equity")

        if equity is not None:
            self.equity_var.set(f"자산: {equity:.2f} USDT")
        else:
            self.equity_var.set("자산: - (조회 필요)")

        if profit is not None:
            sign = "+" if profit >= 0 else ""
            self.profit_var.set(
                f"매매기여 총수익: {sign}{profit:.2f} USDT ({sign}{profit_pct:.2f}%)  (기준 {baseline:.2f} USDT)"
            )
            self.profit_label.config(foreground=GREEN if profit >= 0 else RED)
        elif equity is not None:
            self.profit_var.set("매매기여 총수익: 자금보정 확인 중...")

        symbols_state = snap["symbols"]
        for symbol in _cfg.SYMBOLS:
            enabled = self.symbol_enabled_vars[symbol].get()
            if not enabled:
                if self.tree.exists(symbol):
                    self.tree.item(
                        symbol, values=("비활성", "-", "-", "체크 해제됨 - 매매 안 함"), tags=("disabled_symbol",)
                    )
                continue

            s = symbols_state.get(symbol, {})
            pos = s.get("position")
            if pos:
                pos_str = f"{pos['side']} {pos['contracts']} (PnL {pos['unrealized_pnl']:.2f})"
            else:
                pos_str = "없음"
            action = s.get("last_action") or "-"
            conf = s.get("last_confidence")
            conf_str = f"{conf:.0%}" if isinstance(conf, (int, float)) else "-"
            reasoning = s.get("last_reasoning") or ""
            if self.tree.exists(symbol):
                self.tree.item(symbol, values=(pos_str, action, conf_str, reasoning), tags=())

        self._update_trade_record()

        self.stop_btn.config(state="normal" if running else "disabled")
        self._update_start_enabled()
        self.dry_run_label.config(
            text=f"검토 주기 {_cfg.POLL_INTERVAL_SECONDS}초  ·  레버리지 {_cfg.LEVERAGE}x"
        )

        self.root.after(2000, self.refresh)

    def _update_trade_record(self):
        s = trade_log.stats(_cfg.user_dir, dry_run=False)

        if s["count"] == 0:
            self.record_var.set("아직 청산된 거래가 없습니다")
        else:
            pf = s["profit_factor"]
            pf_str = f"{pf:.2f}" if pf is not None else "∞"
            sign = "+" if s["total_pnl"] >= 0 else ""
            self.record_var.set(
                f"{s['count']}건  ·  승률 {s['win_rate']:.1f}%  ·  "
                f"총손익 {sign}{s['total_pnl']:.2f} USDT  ·  손익비(PF) {pf_str}  ·  "
                f"평균익절 +{s['avg_win']:.2f} / 평균손절 {s['avg_loss']:.2f}"
            )

        self.record_tree.delete(*self.record_tree.get_children())
        for t in trade_log.recent_closed_trades(_cfg.user_dir, dry_run=False, limit=200):
            time_str = str(t.get("time", "")).replace("T", " ")
            pnl = t.get("pnl", 0.0)
            pnl_str = f"{'+' if pnl >= 0 else ''}{pnl:.2f}"
            self.record_tree.insert(
                "",
                "end",
                values=(time_str, t.get("symbol", ""), t.get("side", ""), pnl_str, t.get("reason", "")),
            )


def _enable_mac_clipboard_shortcuts(root: tk.Tk):
    """py2app로 빌드된 앱에서 다른 앱(메모 등)에서 복사한 내용이 붙여넣기 안 되는 문제 대응.
    Tk의 기본 clipboard_get()이 다른 앱이 넣은 클립보드 내용을 못 읽어오는 경우가 있어,
    실패 시 pbpaste로 macOS 클립보드를 직접 읽어오는 방식으로 재시도한다."""

    clip_logger = logging.getLogger("trader.clipboard")
    clip_logger.info("클립보드 진단: PATH=%r", os.environ.get("PATH"))

    def _get_selection_range(widget):
        try:
            return widget.index("sel.first"), widget.index("sel.last")
        except tk.TclError:
            return None

    def _read_clipboard() -> str:
        try:
            text = root.clipboard_get()
            clip_logger.info("clipboard_get() 성공: %d자", len(text))
            if text:
                return text
        except Exception as exc:
            clip_logger.warning("clipboard_get() 실패: %r", exc)
        try:
            result = subprocess.run(
                ["/usr/bin/pbpaste"], capture_output=True, text=True, timeout=2
            )
            clip_logger.info(
                "pbpaste 결과: returncode=%s stdout_len=%d stderr=%r",
                result.returncode,
                len(result.stdout),
                result.stderr,
            )
            return result.stdout
        except Exception as exc:
            clip_logger.warning("pbpaste 실패: %r", exc)
            return ""

    def copy(event):
        widget = event.widget
        try:
            if isinstance(widget, tk.Text):
                text = widget.get("sel.first", "sel.last")
            else:
                text = widget.selection_get()
        except tk.TclError:
            return "break"
        root.clipboard_clear()
        root.clipboard_append(text)
        try:
            subprocess.run(["pbcopy"], input=text, text=True, timeout=2)
        except Exception:
            pass
        return "break"

    def cut(event):
        widget = event.widget
        copy(event)
        sel = _get_selection_range(widget)
        if sel:
            widget.delete(sel[0], sel[1])
        return "break"

    def paste(event):
        widget = event.widget
        clip_logger.info("paste() 호출됨: widget=%r", widget)
        text = _read_clipboard()
        if not text:
            clip_logger.warning("클립보드에서 읽은 내용이 비어있어 붙여넣기 취소")
            return "break"
        sel = _get_selection_range(widget)
        if sel:
            widget.delete(sel[0], sel[1])
        widget.insert("insert", text)
        clip_logger.info("붙여넣기 완료: %d자 삽입", len(text))
        return "break"

    def select_all(event):
        widget = event.widget
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end")
        else:
            widget.select_range(0, "end")
            widget.icursor("end")
        return "break"

    # 가상 이벤트(<<Copy>>/<<Cut>>/<<Paste>>)와 리터럴 키(<Command-v> 등)를 모두 걸어둔다.
    # 메뉴를 통한 실행(<<Paste>>)은 확인됐지만 실제 Cmd+V 키 입력은 안 먹는 게 확인돼서,
    # 리터럴 키 바인딩도 같이 걸어 실제 키 이벤트가 위젯에 도달하는 경로를 직접 잡는다.
    for widget_class in ("TEntry", "TCombobox", "Text"):
        root.bind_class(widget_class, "<<Copy>>", copy)
        root.bind_class(widget_class, "<<Cut>>", cut)
        root.bind_class(widget_class, "<<Paste>>", paste)
        root.bind_class(widget_class, "<Command-a>", select_all)
        root.bind_class(widget_class, "<<SelectAll>>", select_all)

    # Cmd+V가 실제로 Tk까지 도달하는지 진단하기 위해 모든 키 입력을 로그로 남긴다.
    def _log_all_keys(event):
        if event.state & 0x08:  # Command 모디파이어 비트
            clip_logger.info(
                "키 입력 감지: keysym=%r char=%r state=%s widget=%r",
                event.keysym,
                event.char,
                event.state,
                event.widget,
            )

    root.bind_all("<KeyPress>", _log_all_keys, add="+")

    # 우클릭하면 뜨는 붙여넣기 컨텍스트 메뉴 (Edit 메뉴 클릭은 이미 동작 확인됨 — 같은 방식으로
    # Cmd+V가 안 먹는 동안 확실하게 쓸 수 있는 대안 제공).
    context_menu = tk.Menu(root, tearoff=False)

    def _popup_context_menu(event):
        widget = event.widget
        context_menu.delete(0, "end")
        context_menu.add_command(label="Cut", command=lambda: widget.event_generate("<<Cut>>"))
        context_menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
        context_menu.add_command(label="Paste", command=lambda: widget.event_generate("<<Paste>>"))
        context_menu.add_command(
            label="Select All", command=lambda: widget.event_generate("<<SelectAll>>")
        )
        context_menu.tk_popup(event.x_root, event.y_root)

    for widget_class in ("TEntry", "TCombobox", "Text"):
        root.bind_class(widget_class, "<Button-2>", _popup_context_menu)
        root.bind_class(widget_class, "<Control-Button-1>", _popup_context_menu)
        root.bind_class(widget_class, "<Command-c>", copy)
        root.bind_class(widget_class, "<Command-x>", cut)
        root.bind_class(widget_class, "<Command-v>", paste)


def _build_edit_menu(root: tk.Tk):
    """macOS Tk는 앱에 표준 'Edit' 메뉴가 있어야 Cmd+C/X/V/A가 텍스트 위젯에서 실제로 동작한다
    (메뉴가 없으면 단축키 자체가 위젯까지 전달되지 않음 — 이게 진짜 원인이었음)."""

    menu_logger = logging.getLogger("trader.clipboard")

    def _focused():
        widget = root.focus_get()
        menu_logger.info("Edit 메뉴 명령 시점의 포커스 위젯: %r", widget)
        return widget

    def do_cut():
        widget = _focused()
        if widget:
            widget.event_generate("<<Cut>>")

    def do_copy():
        widget = _focused()
        if widget:
            widget.event_generate("<<Copy>>")

    def do_paste():
        widget = _focused()
        if widget:
            widget.event_generate("<<Paste>>")

    def do_select_all():
        widget = _focused()
        if widget:
            widget.event_generate("<<SelectAll>>")

    menubar = tk.Menu(root)
    edit_menu = tk.Menu(menubar, tearoff=False)
    edit_menu.add_command(label="Cut", accelerator="Cmd+X", command=do_cut)
    edit_menu.add_command(label="Copy", accelerator="Cmd+C", command=do_copy)
    edit_menu.add_command(label="Paste", accelerator="Cmd+V", command=do_paste)
    edit_menu.add_command(label="Select All", accelerator="Cmd+A", command=do_select_all)
    menubar.add_cascade(label="Edit", menu=edit_menu)
    root.config(menu=menubar)


def main():
    trader.setup_logging(config.PROJECT_DIR)
    root = tk.Tk()
    _enable_mac_clipboard_shortcuts(root)
    _build_edit_menu(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
