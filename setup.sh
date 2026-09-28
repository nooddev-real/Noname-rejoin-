#!/bin/bash
# Auto-setup if you're feeling lazy.
# Chuyển về thư mục gốc
cd

# Xóa symlink storage cũ nếu có
if [ -e "/data/data/com.termux/files/home/storage" ]; then
	rm -rf /data/data/com.termux/files/home/storage
fi

# Yêu cầu quyền truy cập bộ nhớ (hãy nhấn Cho phép/Allow trên màn hình)
termux-setup-storage
export DEBIAN_FRONTEND=noninteractive

# Chạy script đổi repo
. <(curl -sSL https://gist.githubusercontent.com/nooddev-real/bbe1c2bd8b50733bf9d7b1dbdc921b1e/raw/termux-change-repo.sh)

# 1. FIX: Khôi phục dpkg nếu bị gián đoạn trước đó
dpkg --configure -a

# 2. FIX: Sử dụng cờ -y thay vì 'yes |' để tránh lỗi Broken pipe
pkg update -y && pkg upgrade -y

mkdir -p /storage/emulated/0/Download

# Gộp các gói cần cài đặt lại cho gọn và tối ưu tốc độ
pkg install -y termux-api python python-psutil clang python-cryptography

# 3. Cài đặt các thư viện Python (không cần yes | vì pip không yêu cầu xác nhận Y/n)
pip install --quiet aiohttp requests "python-socketio[asyncio_client]" pycryptodome

DEST_PATH="/storage/emulated/0/Download/noname_rj_main.py"
RAW_URL="https://gist.githubusercontent.com/nooddev-real/d7226923401f61e4d89d9ee11492c106/raw/noname_rj_main.py"

# Tải file Python
curl -sSL "$RAW_URL" -o "$DEST_PATH"

# Kiểm tra kết quả tải file
[ -f "$DEST_PATH" ] && echo "Setup xong!" || echo "Loi: Khong tai duoc file!"
