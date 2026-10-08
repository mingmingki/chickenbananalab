from setuptools import setup

APP = ["app_window.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleExecutable": "AutoTrader",
        "CFBundleName": "AutoTrader",
        "CFBundleDisplayName": "자동매매",
        "CFBundleIdentifier": "com.autotrader.okxgemini",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "",
    },
    "packages": [
        "ccxt",
        "pandas",
        "ta",
        "certifi",
    ],
    "includes": [
        "google.genai",
        "dotenv",
        "cmath",
        "tkinter",
    ],
}

setup(
    app=APP,
    name="AutoTrader",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
