import argparse
import tkinter as tk
from ui.tkinter_ui import GesturePuckApp, UiLogger, check_macos_permissions, resolve_ui_log_path


def build_arg_parser():
    parser = argparse.ArgumentParser(description="GesturePuck macro app")
    parser.add_argument("--port", default=None, help="Serial port, e.g. /dev/cu.usbserial-0001")
    parser.add_argument("--baud", type=int, default=115200, help="ESP32 serial baud for the dual parser")
    parser.add_argument("--auto-connect", action="store_true", help="Connect to the serial port on launch")
    parser.add_argument("--demo", action="store_true", help="Start the synthetic demo source on launch")
    parser.add_argument("--serial-debug", action="store_true", help="Enable serial parser debug logging")
    parser.add_argument("--serial-debug-bytes", action="store_true", help="Include raw serial read chunks in debug logs")
    parser.add_argument("--ui-log", default="auto", help="Tkinter UI log path, directory, 'auto', or 'off'")
    parser.add_argument(
        "--serial-debug-log",
        default=None,
        help="Serial debug log path, directory, or 'auto'",
    )
    return parser


def main():
    args = build_arg_parser().parse_args()
    ui_logger = UiLogger(resolve_ui_log_path(args.ui_log))
    ui_logger.log("startup", f"args={vars(args)}")
    root = tk.Tk()
    check_macos_permissions(ui_logger)
    GesturePuckApp(
        root,
        default_port=args.port,
        auto_connect=args.auto_connect or args.demo,
        demo=args.demo,
        baud=args.baud,
        serial_debug=args.serial_debug,
        serial_debug_log=args.serial_debug_log,
        serial_debug_bytes=args.serial_debug_bytes,
        logger=ui_logger,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
