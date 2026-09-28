# NoName Rejoin Tool

Roblox multi-account manager and auto-rejoin tool for Android (Termux).  
Công cụ quản lý đa tài khoản và tự động Rejoin Roblox trên Android (Termux).

---

### Requirements / Yêu cầu

* Rooted Android device / Thiết bị Android đã Root
* Termux & Termux:API

### Installation / Cài đặt

1. System dependencies / Gói hệ thống:
```bash
pkg update && pkg upgrade -y
pkg install python python-pip clang make libjpeg-turbo termux-api -y
```

2. Python dependencies / Thư viện Python:
```bash
pip install requests psutil aiohttp pillow
```

3. Run / Khởi chạy:
```bash
python main.py
```

### License / Giấy phép

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.  
Dự án được phát hành theo Giấy phép MIT. Xem tệp [LICENSE](LICENSE) để biết chi tiết.
