# Atherloom Android

Android 客户端内置 Atherloom 界面和本地 Standalone 适配层。无需后端即可进入游戏库、手动钓鱼，并在手机上保存基础配置和进度。

GitHub Actions 会在 Android 目录变化时编译 Debug APK，并将其保存为 `Atherloom-Standalone-debug` 构建产物。

聊天模型的原生网络桥、加密数据库和完整备份仍在接入；当前 Standalone 里程碑先验证“下载、打开、进入游戏、离线存档”的闭环。
