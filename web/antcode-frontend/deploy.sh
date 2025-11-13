#!/bin/bash

# 前端部署脚本
set -e

echo "🚀 开始部署前端应用..."

# 检查Node.js版本
echo "📋 检查Node.js版本..."
node --version
npm --version

# 安装依赖
echo "📦 安装依赖..."
npm ci

# 运行类型检查
echo "🔍 运行类型检查..."
npm run build

# 构建成功提示
echo "✅ 前端构建完成！"
echo "📁 构建文件位于: ./dist/"
echo "🌐 可以将dist目录部署到Web服务器"

# 可选：启动预览服务器
read -p "是否启动预览服务器？(y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🔍 启动预览服务器..."
    npm run preview
fi
