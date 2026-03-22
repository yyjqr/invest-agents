sudo apt update
sudo apt install libwebkit2gtk-4.1-dev \
    build-essential \
    curl \
    wget \
    file \
    libssl-dev \
    libgtk-3-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev
# 安装 Tauri CLI
cargo install tauri-cli

# 初始化项目（建议在你的 invest 目录下）
cargo tauri init
