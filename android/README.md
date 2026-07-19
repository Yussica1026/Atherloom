# Atherloom Android

Android 客户端是 Atherloom 的原生 WebView 壳。首次启动填写自己的 Atherloom 后端地址；长按页面可重新配置服务器。

GitHub Actions 会在 Android 目录变化时编译 Debug APK，并将其保存为 `Atherloom-Android-debug` 构建产物。

长期跨网络使用应配置 HTTPS。局域网 HTTP 仅用于私人测试。
