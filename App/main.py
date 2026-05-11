import tkinter as tk
from  ui.tkinter_ui import GesturePuckApp
from ui.tkinter_ui import check_macos_permissions

def main():
    root = tk.Tk()
    check_macos_permissions() 
    app = GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()