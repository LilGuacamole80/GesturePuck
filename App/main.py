import tkinter as tk
from  ui.tkinter_ui import GesturePuckApp

def main():
    root = tk.Tk()
    app = GesturePuckApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()