# CapsLearn 发布构建脚本：打包两个 onedir 程序并压成发布 zip
# 用法：.\build.ps1（需 .venv 里装有 pyinstaller）
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$out = Join-Path $root 'dist-release'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null

& "$root\.venv\Scripts\pyinstaller.exe" --noconfirm --clean --noconsole `
    --name CapsLearn-Capture --distpath "$out\CapsLearn" --workpath "$out\work" `
    --specpath "$out" "$root\capslearn_capture.py"
& "$root\.venv\Scripts\pyinstaller.exe" --noconfirm --clean --console `
    --name CapsLearn-Miner --distpath "$out\CapsLearn" --workpath "$out\work" `
    --specpath "$out" "$root\capslearn_miner.py"

Remove-Item "$out\work" -Recurse -Force
Set-Content -Path "$out\CapsLearn\使用说明.txt" -Encoding utf8 -Value @"
CapsLearn —— CapsWriter-Offline 的自学习纠错回路

1. 把 CapsLearn 文件夹放进 CapsWriter-Offline 目录里（任意子层级均可）
2. 双击 CapsLearn-Capture\CapsLearn-Capture.exe 启动采集端（无窗口，右下角有提示）
3. 改完听写文字后：选中终稿，按 Pause 键（或 Ctrl+Alt+L）
4. 每周跑一次 CapsLearn-Miner\CapsLearn-Miner.exe，重复纠错自动学成热词

文档与源码：https://github.com/Deepmindlearning/capslearn
"@
Compress-Archive -Path "$out\CapsLearn" -DestinationPath "$out\CapsLearn-win64.zip" -Force
Write-Host "done: $out\CapsLearn-win64.zip"
