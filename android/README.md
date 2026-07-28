# Atherloom Android

Android 客户端内置 Atherloom 界面和本地 Standalone 适配层。无需后端即可进入游戏库、手动钓鱼，并在手机上保存基础配置和进度。

GitHub Actions 会在 Android 目录变化时编译 Debug APK，并将其保存为 `Atherloom-Standalone-debug` 构建产物。

当前 Standalone 已接通聊天模型原生网络桥、Android 加密线路存储、选择性备份恢复、游戏与共创空间。发布包必须通过固定签名 GitHub Actions 构建；API Key 不进入明文备份或仓库。
