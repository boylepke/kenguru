"""
Kenguru CAN Monitor — entry point.

Run with:
    python main.py
"""
import tkinter as tk
from kenguru import CANLoggerApp

if __name__ == "__main__":
    root = tk.Tk()
    app  = CANLoggerApp(root)
    root.mainloop()
