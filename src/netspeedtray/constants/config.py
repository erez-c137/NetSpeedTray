"""
Constants for application configuration defaults and constraints.
"""
from typing import Final, Dict, Any, List, Tuple

# --- IMPORT OTHER CONSTANTS TO CREATE A SINGLE SOURCE OF TRUTH ---
from netspeedtray.constants.data import data
from netspeedtray.constants.network import network
from netspeedtray.constants.app import app
from netspeedtray.constants.color import color
from netspeedtray.constants.fonts import fonts
from netspeedtray.constants.lhm import lhm
from netspeedtray.constants.i18n import I18nStrings
from netspeedtray.constants.ui import ui as ui_constants
from netspeedtray.constants.update_mode import UpdateMode


class ConfigMessages:
    # ... (no changes needed in this class)
    """Log message templates for configuration validation."""
    INVALID_NUMERIC: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'"
    INVALID_BOOLEAN: Final[str] = "Invalid {key} '{value}', resetting to boolean default '{default}'"
    INVALID_COLOR: Final[str] = "Invalid color '{value}' for {key}, resetting to default '{default}'"
    INVALID_CHOICE: Final[str] = "Invalid {key} '{value}', resetting to default '{default}'. Valid choices: {choices}"
    INVALID_INTERFACES: Final[str] = "Invalid selected_interfaces value '{value}', resetting to default []"
    INVALID_POSITION: Final[str] = "Invalid {key} '{value}', resetting to None"


    def __init__(self) -> None:
        pass # Validation is not strictly necessary for simple string holders


class ConfigConstants:
    """Defines default values and constraints for all application settings."""
    # --- Schema Versioning ---
    # When the config structure changes, increment this version.
    # The migration system will use this to determine which upgrades to apply.
    # Current version history:
    #   v1.0: Initial schema with all current fields (as of 2026-02-18)
    # 1.2 (2.2): hardware_label_style split into style + hardware_label_color, update_rate snapped to
    # presets. The bump is what makes load() keep a `.bak.v1.1` of the 2.1.x file before migrating it;
    # 2.1.x leaves a newer version untouched (verified against the 2.1.7 tree).
    # 1.3 (2.2 rc.2): the colour scheme - every themed colour is a `<stem>_dark` / `<stem>_light` pair
    # (THEMED_COLOR_STEMS) and the theme no longer writes the text colour into the file. The bump keeps a
    # `.bak.v1.2`; the pre-1.3 colour keys stay in the file, inert, so rc.1 / 2.1.x still read them.
    # 1.4 (2.2 rc.2, legibility package "L"): new factory band colours, font and arrow font. A one-time
    # step (utils/config._migrate_to_1_4) moves only values still on the OLD factory default - a dark High
    # of exactly #00FF00, the font "Segoe UI" (-> "Segoe UI Variable"), the separate 10 pt DemiBold Segoe UI
    # arrow font - to the new
    # defaults; anything else is the user's choice and stays. The bump keeps a `.bak.v1.3` (or `.bak.v1.2`).
    CONFIG_SCHEMA_VERSION: Final[str] = "1.4"
    # A same-version field migration (e.g. dropping a beta's key) rewrites the file without a schema
    # bump. The first `.bak.v<ver>.fields` copy is kept forever (the pristine one); each LATER such
    # migration also snapshots the file as it was just before it, as `.fields.<timestamp>`, and only
    # the newest few of those are kept - enough to undo the last few rewrites, bounded on disk
    # (rc.2 D3). They are taken only when a migration is about to write, never on a plain launch.
    FIELD_MIGRATION_SNAPSHOTS_KEPT: Final[int] = 3

    # --- Default Values for Individual Settings (referencing other constants) ---
    DEFAULT_UPDATE_RATE: Final[float] = 1.0
    DEFAULT_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_FONT_SIZE: Final[int] = 9
    DEFAULT_FONT_WEIGHT: Final[int] = fonts.WEIGHT_NORMAL
    # Off since 1.4 (7b): the arrows are drawn in the text's own font and weight, like the clock's text -
    # the old separate 10 pt DemiBold arrow looked bolted on. Users who set an arrow font keep it.
    DEFAULT_USE_SEPARATE_ARROW_FONT: Final[bool] = False
    DEFAULT_ARROW_FONT_FAMILY: Final[str] = fonts.DEFAULT_FONT
    DEFAULT_ARROW_FONT_SIZE: Final[int] = 10
    DEFAULT_ARROW_FONT_WEIGHT: Final[int] = fonts.WEIGHT_DEMIBOLD
    # Arrows share the speed text's pen by design - one setPen paints the arrow, the number and the
    # unit, so with color-coding on the whole line bands together. Opting in splits that into two
    # color languages (direction vs magnitude), which is a real design change, so it is off by
    # default and existing widgets render byte-identically after an upgrade (#168).
    DEFAULT_USE_CUSTOM_ARROW_COLORS: Final[bool] = False
    # The colour defaults below are the DARK-taskbar ones (the pre-2.2 single values, so a dark widget
    # is unchanged); each themed colour's light-taskbar default is in THEMED_COLOR_DEFAULTS.
    DEFAULT_ARROW_UP_COLOR: Final[str] = color.GREEN
    DEFAULT_ARROW_DOWN_COLOR: Final[str] = color.WHITE
    DEFAULT_COLOR: Final[str] = color.WHITE # Referencing color palette
    DEFAULT_COLOR_CODING: Final[bool] = False
    DEFAULT_HIGH_SPEED_THRESHOLD: Final[float] = 10.0
    DEFAULT_LOW_SPEED_THRESHOLD: Final[float] = 1.0
    # Optional third level between Low and High (#9). 0 = Off: three bands, exactly as before.
    DEFAULT_MEDIUM_SPEED_THRESHOLD: Final[float] = 0.0
    DEFAULT_HIGH_SPEED_COLOR: Final[str] = color.HIGH_SPEED_DARK   # #00FF00 before 1.4 (see color.py)
    DEFAULT_MEDIUM_SPEED_COLOR: Final[str] = color.AMBER
    DEFAULT_LOW_SPEED_COLOR: Final[str] = color.ORANGE # Referencing color palette

    # --- The colour scheme (2.2 rc.2; #117, #332, #9) ---
    # Every foreground colour the widget paints has a dark-taskbar and a light-taskbar value, stored as
    # `<stem>_dark` / `<stem>_light`; utils/color_scheme.resolve() picks the half for the current WINDOWS
    # mode (SystemUsesLightTheme - the taskbar's theme, not the app mode). The theme never writes the
    # config. The stems are the pre-2.2 key names, so RenderConfig's field names did not change. The
    # light defaults are readable on a light taskbar (constants/color.py has the contrast figures).
    # Kept global, on purpose: background_color (a surface, usually transparent) and the custom CPU/GPU
    # label colours (their Brand default already follows the taskbar).
    THEME_SUFFIXES: Final[Tuple[str, str]] = ("dark", "light")
    THEMED_COLOR_DEFAULTS: Final[Dict[str, Tuple[str, str]]] = {   # stem: (dark, light)
        "default_color": (DEFAULT_COLOR, color.TEXT_LIGHT),
        "high_speed_color": (DEFAULT_HIGH_SPEED_COLOR, color.HIGH_SPEED_LIGHT),
        "medium_speed_color": (DEFAULT_MEDIUM_SPEED_COLOR, color.MEDIUM_SPEED_LIGHT),
        "low_speed_color": (DEFAULT_LOW_SPEED_COLOR, color.LOW_SPEED_LIGHT),
        "graph_upload_color": (color.WIDGET_UPLOAD_LINE_DARK, color.WIDGET_UPLOAD_LINE_LIGHT),
        "graph_download_color": (color.DOWNLOAD_LINE_COLOR, color.DOWNLOAD_LINE_LIGHT),
        "arrow_up_color": (DEFAULT_ARROW_UP_COLOR, color.ARROW_UP_LIGHT),
        "arrow_down_color": (DEFAULT_ARROW_DOWN_COLOR, color.TEXT_LIGHT),
    }
    THEMED_COLOR_STEMS: Final[Tuple[str, ...]] = tuple(THEMED_COLOR_DEFAULTS)
    # The pre-1.3 single-value keys. No longer read or written: the 1.3 migration seeds the pairs from
    # them, then they stay in the file untouched (and unvalidated) so a rollback to rc.1 / 2.1.x still
    # finds the colours it had. test_color_scheme guards that no module reads them.
    LEGACY_COLOR_KEYS: Final[Tuple[str, ...]] = (
        "default_color", "color_is_automatic", "high_speed_color", "low_speed_color",
        "arrow_up_color", "arrow_down_color",
    )
    # The factory value each pre-1.3 single key shipped with (the 1.2 -> 1.3 seeding's "nobody chose this"
    # test). Pinned here, NOT read from the current defaults: High's dark default moved in 1.4, and
    # comparing a 2.1.x file's #00FF00 against the new mint would have copied the old green to both
    # taskbars as if the user had picked it.
    LEGACY_COLOR_FACTORY: Final[Dict[str, str]] = {
        "high_speed_color": color.LEGACY_HIGH_SPEED_DARK, "low_speed_color": color.ORANGE,
        "arrow_up_color": color.GREEN, "arrow_down_color": color.WHITE,
    }
    # The 1.4 step (legibility "L"): config key -> (old factory value, new default). Applied once, to a
    # file older than 1.4, and only where the value is EXACTLY the old factory one (compared
    # case-insensitively). Medium and the light colours never shipped before 1.3 (#441), so they need none.
    MIGRATE_1_4_DEFAULTS: Final[Dict[str, Tuple[str, str]]] = {
        "high_speed_color_dark": (color.LEGACY_HIGH_SPEED_DARK, color.HIGH_SPEED_DARK),
        "font_family": (fonts.LEGACY_DEFAULT_FONT, fonts.DEFAULT_FONT),
        "arrow_font_family": (fonts.LEGACY_DEFAULT_FONT, fonts.DEFAULT_FONT),
    }
    # ...and the separate arrow font, on only because it was the old default: on, at exactly the old
    # family / size / weight -> off (the arrows then follow the text font, 7b).
    LEGACY_ARROW_FONT: Final[Tuple[str, int, int]] = (fonts.LEGACY_DEFAULT_FONT, 10, fonts.WEIGHT_DEMIBOLD)
    DEFAULT_BACKGROUND_COLOR: Final[str] = "#000000" # Black
    DEFAULT_BACKGROUND_OPACITY: Final[int] = 0 # Percentage (0-100), default transparent
    DEFAULT_GRAPH_ENABLED: Final[bool] = False
    DEFAULT_HISTORY_MINUTES: Final[int] = 3
    DEFAULT_GRAPH_OPACITY: Final[int] = 66
    # Fixed y-axis for the taskbar mini graph (DU Meter-style). Off by default: the graph keeps
    # auto-fitting to the busiest recent sample, so nobody's graph changes on upgrade. When on, the
    # axis tops out at the advertised plan speed (plan_down_mbps / plan_up_mbps) plus the headroom,
    # with an optional dotted line at the plan speed itself. No plan set = stays automatic.
    DEFAULT_GRAPH_FIXED_SCALE: Final[bool] = False
    DEFAULT_GRAPH_SCALE_HEADROOM: Final[int] = 10   # percent above the plan speed
    GRAPH_SCALE_HEADROOM_CHOICES: Final[List[int]] = [0, 10, 20, 30]
    DEFAULT_GRAPH_SHOW_CEILING: Final[bool] = True  # only drawn when the headroom is above 0%
    # Separate upload/download graphs (DU Meter-style), each with its own scale. Off by default so no
    # widget changes size on upgrade. Side by side on a horizontal taskbar, stacked on a vertical one;
    # Auto-Cycle rotates upload and download as their own phases instead, so it keeps its size.
    DEFAULT_GRAPH_SPLIT_DIRECTIONS: Final[bool] = False
    # Mini graph look: "detailed" plots every sample (the classic filled, spiky graph); "trend" smooths
    # each series into a calm line (widget_renderer.trend_smooth). Trend is the default (owner, #395),
    # also for existing configs without the key; Detailed is one click away in Settings.
    GRAPH_STYLE_CHOICES: Final[List[str]] = ["detailed", "trend"]
    DEFAULT_GRAPH_STYLE: Final[str] = "trend"
    DEFAULT_INTERFACE_MODE: Final[str] = network.interface.DEFAULT_MODE
    DEFAULT_KEEP_DATA_DAYS: Final[int] = data.retention.DEFAULT_DAYS # 365 days (1 Year) default
    DEFAULT_DARK_MODE: Final[bool] = True
    DEFAULT_DYNAMIC_UPDATE_ENABLED: Final[bool] = True
    DEFAULT_SPEED_DISPLAY_MODE: Final[str] = "always_mbps"  # Prevents constant B/KB/MB jumping
    DEFAULT_UNIT_TYPE: Final[str] = "bits_decimal"  # Default to Bits (Kbps, Mbps)
    DEFAULT_SWAP_UPLOAD_DOWNLOAD: Final[bool] = False
    DEFAULT_HIDE_ARROWS: Final[bool] = False
    DEFAULT_HIDE_UNIT_SUFFIX: Final[bool] = False
    DEFAULT_SHORT_UNIT_LABELS: Final[bool] = False
    DEFAULT_DECIMAL_PLACES: Final[int] = 1
    DEFAULT_TEXT_ALIGNMENT: Final[str] = "center"
    DEFAULT_FREE_MOVE: Final[bool] = False
    # When the preferred monitor has NO taskbar of its own (e.g. the Corsair Xeneon Edge, or a
    # secondary display with "show taskbar on all displays" off), float the widget on THAT display
    # instead of falling back to the primary taskbar. On by default so the accessory-display case
    # just works; a user can turn it off to keep the old primary-taskbar fallback (#188).
    DEFAULT_FREE_FLOAT: Final[bool] = True
    DEFAULT_KEEP_VISIBLE_FULLSCREEN: Final[bool] = False
    # #24: on a native horizontal Win11 taskbar, hide while the app buttons reach the widget (the
    # owner's "yield" choice, rc.2 W17). Off = the widget stays put and may cover them (pre-rc.2).
    DEFAULT_HIDE_WHEN_APPS_COVER: Final[bool] = True
    # "Show on all taskbars" (#229): a paint-only copy of the widget on every OTHER taskbar (Windows'
    # "Show my taskbar on all displays"). Off by default - with it off no mirror code runs at all.
    DEFAULT_SHOW_ON_ALL_TASKBARS: Final[bool] = False
    DEFAULT_FORCE_DECIMALS: Final[bool] = True
    # ON by default: a taskbar status widget people want always-present should come back after a reboot.
    # First launch reconciles this with the actual HKCU Run key (main.py → StartupManager.synchronize_startup_task),
    # so a fresh install auto-registers. Existing users keep their saved value (only fresh configs see this).
    DEFAULT_START_WITH_WINDOWS: Final[bool] = True
    DEFAULT_TRAY_OFFSET_X: Final[int] = 0
    DEFAULT_TRAY_OFFSET_Y: Final[int] = 3
    # Pre-1.3.0 tray_offset_x defaults (10 up to 1.2.0, then 1, then 3; 0 since 1.3.0). Every
    # config those versions saved carries one of them although the user never moved the widget, so
    # Reset position (shown only once the widget was moved) treats them as the default too (rc.2 W6).
    # Not migrated: a value that later releases kept placing the widget with stays where it was.
    LEGACY_DEFAULT_TRAY_OFFSETS_X: Final[frozenset] = frozenset({10, 1, 3})
    # A Win11 secondary-monitor taskbar has no tray/clock child HWND to hug, so on a preferred
    # secondary display we reserve this many logical px off the screen edge to clear its clock (#186).
    DEFAULT_SECONDARY_CLOCK_RESERVE_PX: Final[int] = 75
    DEFAULT_LEGEND_POSITION: Final[str] = data.legend_position.DEFAULT_LEGEND_POSITION
    DEFAULT_SHOW_LEGEND: Final[bool] = False

    # --- Configurable widget click actions (community PR #165, thanks @rami123) ---
    # Rami's PR was authored against the pre-2.0 app, so its actions were "show_graph" /
    # "show_app_activity". Those windows were folded into the unified Monitor for 2.0, so the
    # action set is adapted here: Open Monitor / Open Settings / Pause-Resume / Nothing.
    CLICK_ACTION_NONE: Final[str] = "none"
    CLICK_ACTION_OPEN_MONITOR: Final[str] = "open_monitor"
    CLICK_ACTION_SETTINGS: Final[str] = "settings"
    CLICK_ACTION_PAUSE: Final[str] = "pause_resume"
    CLICK_ACTION_CHOICES: Final[List[str]] = [
        CLICK_ACTION_OPEN_MONITOR,
        CLICK_ACTION_SETTINGS,
        CLICK_ACTION_PAUSE,
        CLICK_ACTION_NONE,
    ]
    # Defaults preserve existing behavior: double-click already opened the Monitor; middle-click
    # did nothing, so it stays "Nothing" until a user opts in.
    DEFAULT_DOUBLE_CLICK_ACTION: Final[str] = CLICK_ACTION_OPEN_MONITOR
    DEFAULT_MIDDLE_CLICK_ACTION: Final[str] = CLICK_ACTION_NONE

    # --- CPU/GPU Monitoring ---
    DEFAULT_MONITOR_CPU_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_GPU_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_RAM_ENABLED: Final[bool] = False
    DEFAULT_MONITOR_VRAM_ENABLED: Final[bool] = False
    # #250: RAM / VRAM labels on the memory values. Off by default so no existing widget
    # changes width on upgrade.
    DEFAULT_SHOW_MEMORY_LABELS: Final[bool] = False
    DEFAULT_SHOW_HARDWARE_TEMPS: Final[bool] = True
    DEFAULT_SHOW_HARDWARE_POWER: Final[bool] = False
    # Temperature unit for every temperature the app SHOWS (taskbar, hover card, Monitor, Settings'
    # throttle slider). Stored values stay Celsius everywhere - sensors, history DB, throttle_temp_c,
    # the CSV/JSON export (a stable machine-readable unit, already labelled "°C") - and only the
    # formatter converts (rc.2 F8; the v1.4.0 "Fahrenheit support is planned" promise, O6).
    TEMPERATURE_UNIT_CHOICES: Final[List[str]] = ["celsius", "fahrenheit"]
    DEFAULT_TEMPERATURE_UNIT: Final[str] = "celsius"
    # GPU picker (#236): "" = Automatic (busiest engine on any adapter - the pre-picker readout), or
    # the DXGI Description of the adapter to show. See constants.gpu.ADAPTER_AUTO.
    DEFAULT_GPU_ADAPTER: Final[str] = ""
    DEFAULT_STACK_HARDWARE_STATS: Final[bool] = True
    # Side-taskbar design phase C: keep the taskbar widget to network speed and show the enabled
    # CPU/GPU/memory in the hover card instead. Off by default, so nobody's widget changes.
    DEFAULT_HARDWARE_IN_HOVER_CARD: Final[bool] = False
    # CPU/GPU label SHAPE and COLOUR are two settings (2.2): the style is "icons" | "text", the
    # colour "brand" | "match_text" | "custom". The pre-2.2 three-way style (icons_colored /
    # icons_monochrome / text) is split in two by ConfigManager._migrate_config.
    HARDWARE_LABEL_STYLE_CHOICES: Final[List[str]] = ["icons", "text"]
    DEFAULT_HARDWARE_LABEL_STYLE: Final[str] = "icons"
    HARDWARE_LABEL_COLOR_CHOICES: Final[List[str]] = ["brand", "match_text", "custom"]
    # New installs get Brand (owner call, 2.2). Existing configs are migrated by their old style instead:
    # icons_monochrome users keep their look as match_text (which, unlike the old hard-coded white,
    # stays visible on a LIGHT taskbar); the coloured styles become brand.
    DEFAULT_HARDWARE_LABEL_COLOR: Final[str] = "brand"
    # Custom picks start at the pre-2.2 neutral label colours (== constants.renderer.CPU/GPU_LINE_COLOR).
    DEFAULT_CPU_LABEL_COLOR: Final[str] = "#3498DB"
    DEFAULT_GPU_LABEL_COLOR: Final[str] = "#E74C3C"
    DEFAULT_WIDGET_DISPLAY_MODE: Final[str] = "side_by_side" # Choices: network_only, cycle, side_by_side
    DEFAULT_WIDGET_DISPLAY_ORDER: Final[List[str]] = ["network", "cpu", "gpu"]
    DEFAULT_WIDGET_CYCLE_INTERVAL: Final[int] = 3 # Seconds
    DEFAULT_CPU_LOAD_HIGH_THRESHOLD: Final[float] = 80.0
    DEFAULT_CPU_LOAD_LOW_THRESHOLD: Final[float] = 50.0
    DEFAULT_GPU_LOAD_HIGH_THRESHOLD: Final[float] = 80.0
    DEFAULT_GPU_LOAD_LOW_THRESHOLD: Final[float] = 50.0

    CONFIG_FILENAME: Final[str] = "NetSpeedTray_Config.json"
    
    DEFAULT_CONFIG: Final[Dict[str, Any]] = {
        "config_version": CONFIG_SCHEMA_VERSION,
        "start_with_windows": DEFAULT_START_WITH_WINDOWS,
        "language": None,  # None means auto-detect
        "update_rate": DEFAULT_UPDATE_RATE,
        "font_family": DEFAULT_FONT_FAMILY,
        "font_size": DEFAULT_FONT_SIZE,
        "font_weight": DEFAULT_FONT_WEIGHT,
        "color_coding": DEFAULT_COLOR_CODING,
        "high_speed_threshold": DEFAULT_HIGH_SPEED_THRESHOLD,
        "medium_speed_threshold": DEFAULT_MEDIUM_SPEED_THRESHOLD,
        "low_speed_threshold": DEFAULT_LOW_SPEED_THRESHOLD,
        "graph_enabled": DEFAULT_GRAPH_ENABLED,
        "history_minutes": DEFAULT_HISTORY_MINUTES,
        "graph_opacity": DEFAULT_GRAPH_OPACITY,
        "graph_fixed_scale": DEFAULT_GRAPH_FIXED_SCALE,
        "graph_scale_headroom": DEFAULT_GRAPH_SCALE_HEADROOM,
        "graph_show_ceiling": DEFAULT_GRAPH_SHOW_CEILING,
        "graph_split_directions": DEFAULT_GRAPH_SPLIT_DIRECTIONS,
        "graph_style": DEFAULT_GRAPH_STYLE,
        "interface_mode": DEFAULT_INTERFACE_MODE,
        "selected_interfaces": [],
        "excluded_interfaces": network.interface.DEFAULT_EXCLUSIONS,
        "keep_data": DEFAULT_KEEP_DATA_DAYS,
        "reduce_motion": False,            # app-wide: disable preview/hold-still animations
        # ONE flag for both the Settings dialog and the Monitor window. A per-window pair would
        # double the config surface and the translation cost for no real benefit - someone who
        # wants the Monitor pinned while they work wants the same of Settings (#213).
        "keep_windows_on_top": False,
        "show_usage_on_hover": True,       # the hover card's data-usage rows (Today / This month)
        "power_display_enabled": False,    # opt-in: the hover card's battery rows (live draw / projected)
        "show_hover_tips": True,           # the hover card's right-click/double-click gesture hint
        "pause_in_menu": False,            # opt-in: surface Pause/Resume in the right-click menu
        "dark_mode": DEFAULT_DARK_MODE,
        "history_period": data.history_period.DEFAULT_PERIOD,
        "legend_position": DEFAULT_LEGEND_POSITION,
        "position_x": None,
        "position_y": None,
        "paused": False,
        "dynamic_update_enabled": DEFAULT_DYNAMIC_UPDATE_ENABLED,
        "speed_display_mode": DEFAULT_SPEED_DISPLAY_MODE,
        "decimal_places": DEFAULT_DECIMAL_PLACES,
        "text_alignment": DEFAULT_TEXT_ALIGNMENT,
        "free_move": DEFAULT_FREE_MOVE,
        "free_float": DEFAULT_FREE_FLOAT,
        "double_click_action": DEFAULT_DOUBLE_CLICK_ACTION,
        "middle_click_action": DEFAULT_MIDDLE_CLICK_ACTION,
        "hardware_label_style": DEFAULT_HARDWARE_LABEL_STYLE,
        "hardware_label_color": DEFAULT_HARDWARE_LABEL_COLOR,
        "cpu_label_color": DEFAULT_CPU_LABEL_COLOR,
        "gpu_label_color": DEFAULT_GPU_LABEL_COLOR,
        "keep_visible_fullscreen": DEFAULT_KEEP_VISIBLE_FULLSCREEN,
        "hide_when_apps_cover": DEFAULT_HIDE_WHEN_APPS_COVER,
        "force_decimals": DEFAULT_FORCE_DECIMALS,
        "unit_type": DEFAULT_UNIT_TYPE,
        "swap_upload_download": DEFAULT_SWAP_UPLOAD_DOWNLOAD,
        "hide_arrows": DEFAULT_HIDE_ARROWS,
        "hide_unit_suffix": DEFAULT_HIDE_UNIT_SUFFIX,
        "background_color": DEFAULT_BACKGROUND_COLOR,
        "background_opacity": DEFAULT_BACKGROUND_OPACITY,
        "short_unit_labels": DEFAULT_SHORT_UNIT_LABELS,
        "tray_offset_x": DEFAULT_TRAY_OFFSET_X,
        "tray_offset_y": DEFAULT_TRAY_OFFSET_Y,
        "secondary_clock_reserve_px": DEFAULT_SECONDARY_CLOCK_RESERVE_PX,
        "graph_window_pos": None,
        "settings_window_pos": None,
        "app_activity_window_pos": None,
        "monitor_window_pos": None,
        "monitor_active_tab": None,    # last Monitor tab the user left (overview/network/hardware)
        # Monitor Hardware graph (6.x). Colors None == vendor-auto (AMD red / Intel blue / Nvidia green).
        "monitor_hw_graph_mode": "combined",   # combined | separate | toggle
        "monitor_cpu_graph_color": None,
        "monitor_gpu_graph_color": None,
        "monitor_graph_legend": True,          # show the CPU/GPU legend on the combined graph
        "monitor_graph_smoothing": False,      # smooth (monotone-cubic) vs raw lines on the hw graph
        "monitor_graph_fixed_axis": True,      # hw graph y-axis fixed 0-100% vs auto-scale to the data
        "history_period_slider_value": 0,  # UI-specific state
        # True once the user picks a Monitor period (any tab). Until then the Overview opens on 24h
        # rather than the stored 0 - which is also Session's index, so 0 alone can't say which (M2).
        "history_period_chosen": False,
        "show_legend": DEFAULT_SHOW_LEGEND,
        "use_separate_arrow_font": DEFAULT_USE_SEPARATE_ARROW_FONT,
        "arrow_font_family": DEFAULT_ARROW_FONT_FAMILY,
        "arrow_font_size": DEFAULT_ARROW_FONT_SIZE,
        "arrow_font_weight": DEFAULT_ARROW_FONT_WEIGHT,
        # Custom arrow glyphs (#129). Empty = the native i18n arrow (the Windows default).
        "use_custom_arrow_colors": DEFAULT_USE_CUSTOM_ARROW_COLORS,
        "arrow_up_symbol": "",
        "arrow_down_symbol": "",
        "monitor_cpu_enabled": DEFAULT_MONITOR_CPU_ENABLED,
        "monitor_gpu_enabled": DEFAULT_MONITOR_GPU_ENABLED,
        "monitor_ram_enabled": DEFAULT_MONITOR_RAM_ENABLED,
        "monitor_vram_enabled": DEFAULT_MONITOR_VRAM_ENABLED,
        "show_memory_labels": DEFAULT_SHOW_MEMORY_LABELS,
        "show_hardware_temps": DEFAULT_SHOW_HARDWARE_TEMPS,
        "show_hardware_power": DEFAULT_SHOW_HARDWARE_POWER,
        "temperature_unit": DEFAULT_TEMPERATURE_UNIT,
        "gpu_adapter": DEFAULT_GPU_ADAPTER,
        # LibreHardwareMonitor web server (Settings > Hardware > LibreHardwareMonitor connection, G2/G3):
        # the port LHM listens on, and the optional Basic-auth user name and password. The password is
        # never stored in plain text - only as a per-user DPAPI blob (utils/secret_store.py), and the
        # support bundle redacts both credential keys.
        "lhm_port": lhm.HTTP_PORT,
        "lhm_username": "",
        "lhm_password_protected": "",
        # Settings > Advanced > "Record hardware history". Persists hardware samples to the history DB,
        # but ONLY for metrics the user monitors (CPU/GPU/RAM switches, temps/power switches) - see
        # controller.hardware_persist_allowed. Off = nothing hardware is written; live display (widget,
        # mini graph, Monitor tiles) is unaffected. Latency has its own switch (latency_enabled).
        "record_hardware_history": True,
        # Latency: ping the default GATEWAY (LAN-only, never leaves the network) so the Monitor shows
        # connection latency + loss history. The public anchor (true internet latency) is STRICTLY
        # opt-in - pinging an external host is the one thing that "phones home" for a privacy-first app.
        "latency_enabled": True,
        "latency_public_enabled": False,
        "latency_public_host": "1.1.1.1",
        # Pro-stats thresholds (0 = unset, so the related context line stays hidden). The advertised-plan
        # speeds drive "% of time below plan" in the network Stats-detail sheet; the throttle temp drives
        # "above N°C for X" so a thermal-throttling case is provable.
        "plan_down_mbps": 0,
        "plan_up_mbps": 0,
        "throttle_temp_c": 0,
        "stack_hardware_stats": DEFAULT_STACK_HARDWARE_STATS,
        "hardware_in_hover_card": DEFAULT_HARDWARE_IN_HOVER_CARD,
        "widget_display_mode": DEFAULT_WIDGET_DISPLAY_MODE,
        "widget_display_order": DEFAULT_WIDGET_DISPLAY_ORDER,
        "widget_cycle_interval": DEFAULT_WIDGET_CYCLE_INTERVAL,
        # v2.1 network identity: the Wi-Fi band (2.4G/5G) is Location-free; the SSID is gated behind
        # the Windows Location permission, so it is opt-in and defaults off. See releases/v2.1/KICKOFF.md.
        "show_network_identity": False,
        "identity_mode": "band",           # band (Location-free) | ssid (Location-gated) | both
        "band_display": "always",          # always (neutral) | colored (by band) | alert_only (2.4G warning)
        "location_onboarding_dismissed": False,  # user permanently dismissed the SSID/Location explainer
        "widgets_overlap_nudge_shown": False,  # one-time #200 nudge: widget overlaps the Widgets/weather panel
        "side_bar_no_room_notice_shown": False,  # one-time heads-up: a side taskbar's apps leave no free spot
        "app_overlap_notice_shown": False,  # one-time heads-up: the widget stepped aside for app buttons (#24)
        # The tray_offset_x the user dropped the widget at ON the app buttons (#24 drop baseline): the
        # yield stays off for that spot, across restarts, until a probe sees it clear once. None = none.
        "app_overlap_drop_offset": None,
        "cpu_load_high_threshold": DEFAULT_CPU_LOAD_HIGH_THRESHOLD,
        "cpu_load_low_threshold": DEFAULT_CPU_LOAD_LOW_THRESHOLD,
        "gpu_load_high_threshold": DEFAULT_GPU_LOAD_HIGH_THRESHOLD,
        "gpu_load_low_threshold": DEFAULT_GPU_LOAD_LOW_THRESHOLD,
        "check_for_updates": True,
        "skipped_version": None,
        "last_update_check": None,
        "first_run_v2_seen": False,
        "first_run_ever": True,
        "tooltip_hint_shown_count": 0,     # gesture-hint tooltip fades after a few sessions
        "pin_hint_shown_count": 0,         # "Click the widget to keep this open" footer: first few hover cards
        "history_upgrade_notice_shown": False,  # the one "history upgraded" flyout after v7 -> v8
        "temp_onboarding_dismissed": False,  # user opted out of the no-sensor temp explainer
        "preferred_monitor": None,
        "show_on_all_taskbars": DEFAULT_SHOW_ON_ALL_TASKBARS,
        # --- Data-usage / data-cap ---
        "data_cap_enabled": False,
        "data_cap_gb": 0.0,                # the cap in GB (0 = unset)
        "data_cap_reset_day": 1,           # billing reset day-of-month (1-28)
        "data_cap_count": "total",         # total | download | upload
        "data_cap_alert_enabled": True,    # opt-in toast at 80% / 100%
        "usage_alert_state": "",           # internal: "<period_key>:<levels>" for restart-safe alerts
        # --- Local metrics endpoint (2.2 scope B) ---
        # OFF by default: an always-listening socket in a zero-telemetry app has to be the user's
        # deliberate act. When on, it binds 127.0.0.1 only - nothing leaves the machine.
        "metrics_endpoint_enabled": False,
        "metrics_endpoint_port": network.metrics.DEFAULT_PORT,
    }
    
    # The 16 themed colour keys, generated from THEMED_COLOR_DEFAULTS.
    # (Plain loops: a comprehension in a class body cannot see the class's other names.)
    for _stem, _pair in THEMED_COLOR_DEFAULTS.items():
        for _i, _suffix in enumerate(THEME_SUFFIXES):
            DEFAULT_CONFIG[f"{_stem}_{_suffix}"] = _pair[_i]

    # --- Schema Definition for Modern Config Validation ---
    VALIDATION_SCHEMA: Final[Dict[str, Dict[str, Any]]] = {
        "config_version": {"type": str, "default": CONFIG_SCHEMA_VERSION},
        "start_with_windows": {"type": bool, "default": DEFAULT_START_WITH_WINDOWS},
        "language": {"type": (str, type(None)), "default": None, "choices": list(I18nStrings.LANGUAGE_MAP.keys()) + [None]},
        # Seconds between samples: one of the four slider presets (1/2/5/10). `_migrate_config` snaps
        # any other persisted value onto the ladder first, so this only catches a bad in-memory value.
        "update_rate": {"type": (int, float), "default": DEFAULT_UPDATE_RATE, "choices": list(UpdateMode.PRESETS)},
        "font_family": {"type": str, "default": DEFAULT_FONT_FAMILY},
        "font_size": {"type": int, "default": DEFAULT_FONT_SIZE, "min": fonts.FONT_SIZE_MIN, "max": fonts.FONT_SIZE_MAX},
        "font_weight": {"type": int, "default": DEFAULT_FONT_WEIGHT, "min": 1, "max": 1000},
        "color_coding": {"type": bool, "default": DEFAULT_COLOR_CODING},
        "high_speed_threshold": {"type": (int, float), "default": DEFAULT_HIGH_SPEED_THRESHOLD, "min": 0, "max": ui_constants.sliders.SPEED_THRESHOLD_MAX_HIGH},
        # 0 (Off) or strictly between Low and High - the cross-field rule in utils/config.py resets a
        # misplaced Medium alone, never the user's High / Low.
        "medium_speed_threshold": {"type": (int, float), "default": DEFAULT_MEDIUM_SPEED_THRESHOLD, "min": 0, "max": ui_constants.sliders.SPEED_THRESHOLD_MAX_LOW},
        "low_speed_threshold": {"type": (int, float), "default": DEFAULT_LOW_SPEED_THRESHOLD, "min": 0, "max": ui_constants.sliders.SPEED_THRESHOLD_MAX_LOW},
        "graph_enabled": {"type": bool, "default": DEFAULT_GRAPH_ENABLED},
        "history_minutes": {"type": int, "default": DEFAULT_HISTORY_MINUTES, "min": 1, "max": 1440}, # Range from manual check
        # "coerce": accepted as int or float (older files / hand edits), stored as a rounded int - the
        # Settings slider and every consumer treat it as a whole percent (C1).
        "graph_opacity": {"type": (int, float), "default": DEFAULT_GRAPH_OPACITY, "min": ui_constants.sliders.OPACITY_MIN, "max": ui_constants.sliders.OPACITY_MAX, "coerce": int},
        "graph_fixed_scale": {"type": bool, "default": DEFAULT_GRAPH_FIXED_SCALE},
        "graph_scale_headroom": {"type": int, "default": DEFAULT_GRAPH_SCALE_HEADROOM, "choices": GRAPH_SCALE_HEADROOM_CHOICES},
        "graph_show_ceiling": {"type": bool, "default": DEFAULT_GRAPH_SHOW_CEILING},
        "graph_split_directions": {"type": bool, "default": DEFAULT_GRAPH_SPLIT_DIRECTIONS},
        "graph_style": {"type": str, "default": DEFAULT_GRAPH_STYLE, "choices": GRAPH_STYLE_CHOICES},
        "interface_mode": {"type": str, "default": DEFAULT_INTERFACE_MODE, "choices": list(network.interface.VALID_INTERFACE_MODES)},
        "selected_interfaces": {"type": list, "default": [], "item_type": str},
        "excluded_interfaces": {"type": list, "default": network.interface.DEFAULT_EXCLUSIONS, "item_type": str},
        "keep_data": {"type": int, "default": DEFAULT_KEEP_DATA_DAYS, "choices": list(data.retention.DAYS_MAP.values()), "min": min(data.retention.DAYS_MAP.values()), "max": max(data.retention.DAYS_MAP.values())},
        "reduce_motion": {"type": bool, "default": False},
        "keep_windows_on_top": {"type": bool, "default": False},
        "show_usage_on_hover": {"type": bool, "default": True},
        "power_display_enabled": {"type": bool, "default": False},
        "show_hover_tips": {"type": bool, "default": True},
        "pause_in_menu": {"type": bool, "default": False},
        "dark_mode": {"type": bool, "default": DEFAULT_DARK_MODE},
        "history_period": {"type": str, "default": data.history_period.DEFAULT_PERIOD, "choices": list(data.history_period.PERIOD_MAP.values())},
        "legend_position": {"type": str, "default": DEFAULT_LEGEND_POSITION, "choices": data.legend_position.UI_OPTIONS},
        "position_x": {"type": (int, type(None)), "default": None},
        "position_y": {"type": (int, type(None)), "default": None},
        "paused": {"type": bool, "default": False},
        "dynamic_update_enabled": {"type": bool, "default": DEFAULT_DYNAMIC_UPDATE_ENABLED},
        "speed_display_mode": {"type": str, "default": DEFAULT_SPEED_DISPLAY_MODE, "choices": ["auto", "always_mbps"]},
        "decimal_places": {"type": int, "default": DEFAULT_DECIMAL_PLACES, "min": 0, "max": 2},
        "text_alignment": {"type": str, "default": DEFAULT_TEXT_ALIGNMENT, "choices": ["left", "center", "right"]},
        "use_separate_arrow_font": {"type": bool, "default": DEFAULT_USE_SEPARATE_ARROW_FONT},
        "arrow_font_family": {"type": str, "default": DEFAULT_ARROW_FONT_FAMILY},
        "arrow_font_size": {"type": int, "default": DEFAULT_ARROW_FONT_SIZE, "min": fonts.FONT_SIZE_MIN, "max": fonts.FONT_SIZE_MAX},
        "arrow_font_weight": {"type": int, "default": DEFAULT_ARROW_FONT_WEIGHT, "min": 1, "max": 1000},
        "use_custom_arrow_colors": {"type": bool, "default": DEFAULT_USE_CUSTOM_ARROW_COLORS},
        "arrow_up_symbol": {"type": str, "default": ""},
        "arrow_down_symbol": {"type": str, "default": ""},
        "free_move": {"type": bool, "default": DEFAULT_FREE_MOVE},
        "free_float": {"type": bool, "default": DEFAULT_FREE_FLOAT},
        "double_click_action": {"type": str, "default": DEFAULT_DOUBLE_CLICK_ACTION, "choices": CLICK_ACTION_CHOICES},
        "middle_click_action": {"type": str, "default": DEFAULT_MIDDLE_CLICK_ACTION, "choices": CLICK_ACTION_CHOICES},
        "hardware_label_style": {"type": str, "default": DEFAULT_HARDWARE_LABEL_STYLE, "choices": HARDWARE_LABEL_STYLE_CHOICES},
        "hardware_label_color": {"type": str, "default": DEFAULT_HARDWARE_LABEL_COLOR, "choices": HARDWARE_LABEL_COLOR_CHOICES},
        "cpu_label_color": {"type": str, "default": DEFAULT_CPU_LABEL_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "gpu_label_color": {"type": str, "default": DEFAULT_GPU_LABEL_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "keep_visible_fullscreen": {"type": bool, "default": DEFAULT_KEEP_VISIBLE_FULLSCREEN},
        "hide_when_apps_cover": {"type": bool, "default": DEFAULT_HIDE_WHEN_APPS_COVER},
        "force_decimals": {"type": bool, "default": DEFAULT_FORCE_DECIMALS},
        "unit_type": {"type": str, "default": DEFAULT_UNIT_TYPE, "choices": ["bits_decimal", "bits_binary", "bytes_decimal", "bytes_binary"]},
        "swap_upload_download": {"type": bool, "default": DEFAULT_SWAP_UPLOAD_DOWNLOAD},
        "hide_arrows": {"type": bool, "default": DEFAULT_HIDE_ARROWS},
        "hide_unit_suffix": {"type": bool, "default": DEFAULT_HIDE_UNIT_SUFFIX},
        "background_color": {"type": str, "default": DEFAULT_BACKGROUND_COLOR, "regex": r"#[0-9a-fA-F]{6}"},
        "background_opacity": {"type": int, "default": DEFAULT_BACKGROUND_OPACITY, "min": 0, "max": 100},
        "short_unit_labels": {"type": bool, "default": DEFAULT_SHORT_UNIT_LABELS},
        # These are DISTANCES FROM THE SCREEN EDGE, not small nudges, so the bound has to cover a
        # whole taskbar. InputHandler._save_dragged_position stores `right_boundary - widget_x -
        # width`, which on a 3840px taskbar is 2000-3000 for any drag toward the left. The old
        # +/-500 cap rejected those and _validate_value reset them to 0, so dragging the widget
        # more than ~500px from the tray silently snapped it back on the next load - one reporter's
        # log had 12 such resets in half an hour (#234). 10000 still catches a corrupt value while
        # accommodating an 8K display.
        # min is negative (PR #165, @rami123): dragging the widget past the tray boundary produces a
        # negative offset; clamping it to 0 snapped the widget back on the next reposition. The same
        # applies to Y on a vertical taskbar, which was still clamped at 0.
        "tray_offset_x": {"type": int, "default": DEFAULT_TRAY_OFFSET_X, "min": -10000, "max": 10000},
        "tray_offset_y": {"type": int, "default": DEFAULT_TRAY_OFFSET_Y, "min": -10000, "max": 10000},
        "secondary_clock_reserve_px": {"type": int, "default": DEFAULT_SECONDARY_CLOCK_RESERVE_PX, "min": 0, "max": 500},
        "graph_window_pos": {"type": (dict, type(None)), "default": None},
        "settings_window_pos": {"type": (dict, type(None)), "default": None},
        "app_activity_window_pos": {"type": (dict, type(None)), "default": None},
        "monitor_window_pos": {"type": (dict, type(None)), "default": None},
        "monitor_active_tab": {"type": (str, type(None)), "default": None},
        "monitor_hw_graph_mode": {"type": str, "default": "combined", "choices": ["combined", "separate", "toggle"]},
        "monitor_cpu_graph_color": {"type": (str, type(None)), "default": None},
        "monitor_gpu_graph_color": {"type": (str, type(None)), "default": None},
        "monitor_graph_legend": {"type": bool, "default": True},
        "monitor_graph_smoothing": {"type": bool, "default": False},
        "monitor_graph_fixed_axis": {"type": bool, "default": True},
        "history_period_slider_value": {"type": int, "default": 0, "min": 0, "max": len(data.history_period.PERIOD_MAP) - 1},
        "history_period_chosen": {"type": bool, "default": False},
        "show_legend": {"type": bool, "default": DEFAULT_SHOW_LEGEND},
        "monitor_cpu_enabled": {"type": bool, "default": DEFAULT_MONITOR_CPU_ENABLED},
        "monitor_gpu_enabled": {"type": bool, "default": DEFAULT_MONITOR_GPU_ENABLED},
        "monitor_ram_enabled": {"type": bool, "default": DEFAULT_MONITOR_RAM_ENABLED},
        "monitor_vram_enabled": {"type": bool, "default": DEFAULT_MONITOR_VRAM_ENABLED},
        "show_memory_labels": {"type": bool, "default": DEFAULT_SHOW_MEMORY_LABELS},
        "show_hardware_temps": {"type": bool, "default": DEFAULT_SHOW_HARDWARE_TEMPS},
        "record_hardware_history": {"type": bool, "default": True},
        "latency_enabled": {"type": bool, "default": True},
        "latency_public_enabled": {"type": bool, "default": False},
        "latency_public_host": {"type": str, "default": "1.1.1.1"},
        "plan_down_mbps": {"type": (int, float), "default": 0, "min": 0, "max": 100000},
        "plan_up_mbps": {"type": (int, float), "default": 0, "min": 0, "max": 100000},
        "throttle_temp_c": {"type": (int, float), "default": 0, "min": 0, "max": 130},
        "show_hardware_power": {"type": bool, "default": DEFAULT_SHOW_HARDWARE_POWER},
        "temperature_unit": {"type": str, "default": DEFAULT_TEMPERATURE_UNIT, "choices": TEMPERATURE_UNIT_CHOICES},
        "gpu_adapter": {"type": str, "default": DEFAULT_GPU_ADAPTER},
        "lhm_port": {"type": int, "default": lhm.HTTP_PORT, "min": lhm.MIN_PORT, "max": lhm.MAX_PORT},
        "lhm_username": {"type": str, "default": ""},
        # No regex on purpose: a failed format check logs the value, and this one must never be logged.
        "lhm_password_protected": {"type": str, "default": ""},
        "stack_hardware_stats": {"type": bool, "default": DEFAULT_STACK_HARDWARE_STATS},
        "hardware_in_hover_card": {"type": bool, "default": DEFAULT_HARDWARE_IN_HOVER_CARD},
        "widget_display_mode": {"type": str, "default": DEFAULT_WIDGET_DISPLAY_MODE, "choices": ["network_only", "cycle", "side_by_side"]},
        "widget_display_order": {"type": list, "default": DEFAULT_WIDGET_DISPLAY_ORDER, "item_type": str},
        "widget_cycle_interval": {"type": int, "default": DEFAULT_WIDGET_CYCLE_INTERVAL, "min": 1, "max": 60},
        "show_network_identity": {"type": bool, "default": False},
        "identity_mode": {"type": str, "default": "band", "choices": ["band", "ssid", "both"]},
        "band_display": {"type": str, "default": "always", "choices": ["always", "colored", "alert_only"]},
        "location_onboarding_dismissed": {"type": bool, "default": False},
        "widgets_overlap_nudge_shown": {"type": bool, "default": False},
        "side_bar_no_room_notice_shown": {"type": bool, "default": False},
        "app_overlap_notice_shown": {"type": bool, "default": False},
        "app_overlap_drop_offset": {"type": (int, type(None)), "default": None},
        "cpu_load_high_threshold": {"type": (int, float), "default": DEFAULT_CPU_LOAD_HIGH_THRESHOLD, "min": 0, "max": 100},
        "cpu_load_low_threshold": {"type": (int, float), "default": DEFAULT_CPU_LOAD_LOW_THRESHOLD, "min": 0, "max": 100},
        "gpu_load_high_threshold": {"type": (int, float), "default": DEFAULT_GPU_LOAD_HIGH_THRESHOLD, "min": 0, "max": 100},
        "gpu_load_low_threshold": {"type": (int, float), "default": DEFAULT_GPU_LOAD_LOW_THRESHOLD, "min": 0, "max": 100},
        "check_for_updates": {"type": bool, "default": True},
        "skipped_version": {"type": (str, type(None)), "default": None},
        "last_update_check": {"type": (str, type(None)), "default": None},
        "first_run_v2_seen": {"type": bool, "default": False},
        "first_run_ever": {"type": bool, "default": True},
        "tooltip_hint_shown_count": {"type": int, "default": 0, "min": 0},
        "pin_hint_shown_count": {"type": int, "default": 0, "min": 0},
        "history_upgrade_notice_shown": {"type": bool, "default": False},
        "temp_onboarding_dismissed": {"type": bool, "default": False},
        # QScreen.name() identifier (e.g. "\\.\DISPLAY1"). None = use primary.
        # If the saved screen isn't found at runtime, fall back to primary.
        "preferred_monitor": {"type": (str, type(None)), "default": None},
        "show_on_all_taskbars": {"type": bool, "default": DEFAULT_SHOW_ON_ALL_TASKBARS},
        # --- Data-usage / data-cap ---
        "data_cap_enabled": {"type": bool, "default": False},
        "data_cap_gb": {"type": (int, float), "default": 0.0, "min": 0.0, "max": 1_000_000.0},
        "data_cap_reset_day": {"type": int, "default": 1, "min": 1, "max": 28},
        "data_cap_count": {"type": str, "default": "total", "choices": ["total", "download", "upload"]},
        "data_cap_alert_enabled": {"type": bool, "default": True},
        "usage_alert_state": {"type": str, "default": ""},
        "metrics_endpoint_enabled": {"type": bool, "default": False},
        "metrics_endpoint_port": {"type": int, "default": network.metrics.DEFAULT_PORT,
                                  "min": network.metrics.MIN_PORT, "max": network.metrics.MAX_PORT},
    }
    # One #RRGGBB rule per themed colour key, so a bad hand edit resets that key, not the scheme.
    for _stem, _pair in THEMED_COLOR_DEFAULTS.items():
        for _i, _suffix in enumerate(THEME_SUFFIXES):
            VALIDATION_SCHEMA[f"{_stem}_{_suffix}"] = {"type": str, "default": _pair[_i],
                                                       "regex": r"#[0-9a-fA-F]{6}"}
    del _stem, _pair, _i, _suffix


    def __init__(self) -> None:
        self.validate()


    def validate(self) -> None:
        if self.DEFAULT_FONT_SIZE < 1:
            raise ValueError("DEFAULT_FONT_SIZE must be positive")
        if not (0 <= self.DEFAULT_GRAPH_OPACITY <= 100):
            raise ValueError("DEFAULT_GRAPH_OPACITY must be between 0 and 100")
        if not self.CONFIG_FILENAME:
             raise ValueError("CONFIG_FILENAME must not be empty")

        actual_keys = set(self.DEFAULT_CONFIG.keys())
        expected_keys = set(self.VALIDATION_SCHEMA.keys())

        if actual_keys != expected_keys:
            missing = expected_keys - actual_keys
            extra = actual_keys - expected_keys
            raise ValueError(f"DEFAULT_CONFIG key mismatch. Missing: {missing or 'None'}. Extra: {extra or 'None'}.")


class ConfigurationConstants:
    """Container for configuration-related constant groups."""
    def __init__(self) -> None:
        self.defaults = ConfigConstants()
        self.messages = ConfigMessages()

# Singleton instance for easy access
config = ConfigurationConstants()
