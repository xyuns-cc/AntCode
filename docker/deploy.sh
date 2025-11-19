#!/bin/bash
# =====================================================
# AntCode Docker 快速部署脚本（前后端分离版本）
# =====================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

print_success() {
    echo -e "${GREEN}✅${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}⚠️${NC} $1"
}

print_error() {
    echo -e "${RED}❌${NC} $1"
}

print_header() {
    echo ""
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

# 检查 Docker 是否安装
check_docker() {
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        print_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi
    
    print_success "Docker 环境检查通过"
}

# 显示主菜单
show_main_menu() {
    print_header "AntCode Docker 管理"
    echo "请选择操作："
    echo ""
    echo "  1) 🚀 快速启动（前端 + 后端）"
    echo "  2) 🏗️  构建镜像"
    echo "  3) 🔧 部署配置选择"
    echo "  4) 📊 查看服务状态"
    echo "  5) 📋 查看日志"
    echo "  6) ⏹️  停止服务"
    echo "  7) 🔄 重启服务"
    echo "  8) 🧹 清理资源"
    echo "  0) 🚪 退出"
    echo ""
}

# 显示构建菜单
show_build_menu() {
    print_header "构建镜像选项"
    echo "请选择要构建的镜像："
    echo ""
    echo "  1) 🔧 构建后端镜像（SQLite）"
    echo "  2) 🔧 构建后端镜像（MySQL）"
    echo "  3) 🔧 构建后端镜像（PostgreSQL）"
    echo "  4) 🎨 构建前端镜像"
    echo "  5) 🏗️  构建所有镜像"
    echo "  0) ⬅️  返回主菜单"
    echo ""
}

# 显示部署配置菜单
show_deploy_menu() {
    print_header "部署配置选择"
    echo "请选择部署配置："
    echo ""
    echo "  1) SQLite + 内存缓存（最简单）"
    echo "  2) SQLite + Redis"
    echo "  3) MySQL + Redis"
    echo "  4) PostgreSQL + Redis"
    echo "  0) ⬅️  返回主菜单"
    echo ""
}

# 快速启动
quick_start() {
    print_header "快速启动 AntCode"
    
    # 检查 .env 文件
    if [ ! -f .env ]; then
        print_warning ".env 文件不存在，从 .env.example 复制..."
        if [ -f .env.example ]; then
            cp .env.example .env
            print_success ".env 文件已创建"
            print_warning "请编辑 .env 文件配置必要的环境变量"
            print_info "特别是 JWT_SECRET_KEY 等敏感信息"
            read -p "按回车键继续..." -r
        else
            print_error ".env.example 文件不存在，无法创建配置文件"
            return 1
        fi
    fi
    
    cd docker
    
    print_info "启动前端和后端服务..."
    docker compose up -d
    
    print_success "服务启动成功！"
    show_access_info
}

# 构建后端镜像
build_backend() {
    local db_type=$1
    print_header "构建后端镜像（数据库类型: $db_type）"
    
    print_info "开始构建后端镜像..."
    docker build -f Dockerfile.backend -t antcode-backend:latest \
        --build-arg DB_TYPE=$db_type .
    
    print_success "后端镜像构建完成！"
}

# 构建前端镜像
build_frontend() {
    print_header "构建前端镜像"
    
    print_info "开始构建前端镜像..."
    cd web/antcode-frontend
    docker build -t antcode-frontend:latest .
    cd ../..
    
    print_success "前端镜像构建完成！"
}

# 构建所有镜像
build_all() {
    print_header "构建所有镜像"
    
    print_info "选择后端数据库类型："
    echo "  1) SQLite（默认）"
    echo "  2) MySQL"
    echo "  3) PostgreSQL"
    read -p "请选择 [1-3]: " db_choice
    
    case $db_choice in
        2) db_type="mysql" ;;
        3) db_type="postgres" ;;
        *) db_type="sqlite" ;;
    esac
    
    build_backend $db_type
    build_frontend
    
    print_success "所有镜像构建完成！"
}

# 处理构建菜单
handle_build_menu() {
    while true; do
        show_build_menu
        read -p "请选择 [0-5]: " choice
        
        case $choice in
            1) build_backend "sqlite"; break ;;
            2) build_backend "mysql"; break ;;
            3) build_backend "postgres"; break ;;
            4) build_frontend; break ;;
            5) build_all; break ;;
            0) return ;;
            *)
                print_error "无效选择，请重新输入"
                sleep 1
                ;;
        esac
    done
}

# SQLite + 内存缓存
deploy_sqlite_memory() {
    print_header "部署：SQLite + 内存缓存"
    print_info "这是最简单的配置，无需额外服务"
    
    cd docker
    docker compose up -d antcode-backend antcode-frontend
    
    print_success "部署完成！"
    show_access_info
}

# SQLite + Redis
deploy_sqlite_redis() {
    print_header "部署：SQLite + Redis"
    
    print_warning "需要在 docker-compose.yml 中取消 Redis 服务的注释"
    print_info "是否已完成配置？(y/n)"
    read -r answer
    
    if [[ "$answer" != "y" ]]; then
        print_warning "请先编辑 docker/docker-compose.yml"
        return
    fi
    
    cd docker
    docker compose up -d redis antcode-backend antcode-frontend
    
    print_success "部署完成！"
    show_access_info
}

# MySQL + Redis
deploy_mysql_redis() {
    print_header "部署：MySQL + Redis"
    
    print_warning "需要在 docker-compose.yml 中取消 MySQL 和 Redis 服务的注释"
    print_warning "并且需要先构建带 MySQL 支持的后端镜像"
    print_info "是否已完成配置？(y/n)"
    read -r answer
    
    if [[ "$answer" != "y" ]]; then
        print_warning "请先完成配置"
        return
    fi
    
    cd docker
    docker compose up -d mysql redis antcode-backend antcode-frontend
    
    print_success "部署完成！"
    show_access_info
}

# PostgreSQL + Redis
deploy_postgres_redis() {
    print_header "部署：PostgreSQL + Redis"
    
    print_warning "需要在 docker-compose.yml 中取消 PostgreSQL 和 Redis 服务的注释"
    print_warning "并且需要先构建带 PostgreSQL 支持的后端镜像"
    print_info "是否已完成配置？(y/n)"
    read -r answer
    
    if [[ "$answer" != "y" ]]; then
        print_warning "请先完成配置"
        return
    fi
    
    cd docker
    docker compose up -d postgres redis antcode-backend antcode-frontend
    
    print_success "部署完成！"
    show_access_info
}

# 处理部署菜单
handle_deploy_menu() {
    while true; do
        show_deploy_menu
        read -p "请选择 [0-4]: " choice
        
        case $choice in
            1) deploy_sqlite_memory; break ;;
            2) deploy_sqlite_redis; break ;;
            3) deploy_mysql_redis; break ;;
            4) deploy_postgres_redis; break ;;
            0) return ;;
            *)
                print_error "无效选择，请重新输入"
                sleep 1
                ;;
        esac
    done
}

# 显示访问信息
show_access_info() {
    echo ""
    print_header "服务访问信息"
    echo "  🌐 前端地址: http://localhost:3000"
    echo "  📚 后端 API: http://localhost:8000"
    echo "  📖 API 文档: http://localhost:8000/docs"
    echo "  👤 默认账号: admin / admin"
    echo ""
    print_info "查看日志: cd docker && docker compose logs -f"
    print_info "停止服务: cd docker && docker compose down"
    echo ""
}

# 查看状态
show_status() {
    print_header "服务状态"
    cd docker
    docker compose ps
    echo ""
    
    print_info "容器详细信息："
    echo ""
    
    # 后端状态
    if docker ps --filter "name=antcode-backend" --format "{{.Names}}" | grep -q antcode-backend; then
        echo "🟢 后端服务: 运行中"
        docker ps --filter "name=antcode-backend" --format "   容器: {{.Names}} | 状态: {{.Status}} | 端口: {{.Ports}}"
    else
        echo "🔴 后端服务: 未运行"
    fi
    
    # 前端状态
    if docker ps --filter "name=antcode-frontend" --format "{{.Names}}" | grep -q antcode-frontend; then
        echo "🟢 前端服务: 运行中"
        docker ps --filter "name=antcode-frontend" --format "   容器: {{.Names}} | 状态: {{.Status}} | 端口: {{.Ports}}"
    else
        echo "🔴 前端服务: 未运行"
    fi
    
    echo ""
    print_info "查看详细日志: cd docker && docker compose logs -f [service-name]"
}

# 查看日志
show_logs() {
    print_header "查看日志"
    echo "请选择要查看的服务："
    echo ""
    echo "  1) 后端日志"
    echo "  2) 前端日志"
    echo "  3) 所有服务日志"
    echo "  0) 返回"
    echo ""
    read -p "请选择 [0-3]: " choice
    
    cd docker
    case $choice in
        1) docker compose logs -f antcode-backend ;;
        2) docker compose logs -f antcode-frontend ;;
        3) docker compose logs -f ;;
        0) return ;;
        *) print_error "无效选择" ;;
    esac
}

# 停止服务
stop_services() {
    print_header "停止服务"
    echo "请选择停止选项："
    echo ""
    echo "  1) 停止所有服务（保留数据）"
    echo "  2) 停止后端服务"
    echo "  3) 停止前端服务"
    echo "  4) 停止并删除数据卷（危险！）"
    echo "  0) 返回"
    echo ""
    read -p "请选择 [0-4]: " choice
    
    cd docker
    case $choice in
        1)
            docker compose down
            print_success "所有服务已停止"
            ;;
        2)
            docker compose stop antcode-backend
            docker compose rm -f antcode-backend
            print_success "后端服务已停止"
            ;;
        3)
            docker compose stop antcode-frontend
            docker compose rm -f antcode-frontend
            print_success "前端服务已停止"
            ;;
        4)
            print_warning "这将删除所有数据！"
            read -p "确认删除？(yes/no): " confirm
            if [[ "$confirm" == "yes" ]]; then
                docker compose down -v
                print_success "服务已停止并删除数据卷"
            else
                print_info "操作已取消"
            fi
            ;;
        0) return ;;
        *) print_error "无效选择" ;;
    esac
}

# 重启服务
restart_services() {
    print_header "重启服务"
    echo "请选择重启选项："
    echo ""
    echo "  1) 重启所有服务"
    echo "  2) 重启后端服务"
    echo "  3) 重启前端服务"
    echo "  0) 返回"
    echo ""
    read -p "请选择 [0-3]: " choice
    
    cd docker
    case $choice in
        1)
            docker compose restart
            print_success "所有服务已重启"
            ;;
        2)
            docker compose restart antcode-backend
            print_success "后端服务已重启"
            ;;
        3)
            docker compose restart antcode-frontend
            print_success "前端服务已重启"
            ;;
        0) return ;;
        *) print_error "无效选择" ;;
    esac
}

# 清理资源
cleanup_resources() {
    print_header "清理资源"
    echo "请选择清理选项："
    echo ""
    echo "  1) 清理未使用的镜像"
    echo "  2) 清理未使用的容器"
    echo "  3) 清理未使用的数据卷"
    echo "  4) 清理所有未使用资源（慎用）"
    echo "  0) 返回"
    echo ""
    read -p "请选择 [0-4]: " choice
    
    case $choice in
        1)
            docker image prune -f
            print_success "未使用的镜像已清理"
            ;;
        2)
            docker container prune -f
            print_success "未使用的容器已清理"
            ;;
        3)
            print_warning "这将删除所有未使用的数据卷！"
            read -p "确认删除？(yes/no): " confirm
            if [[ "$confirm" == "yes" ]]; then
                docker volume prune -f
                print_success "未使用的数据卷已清理"
            else
                print_info "操作已取消"
            fi
            ;;
        4)
            print_warning "这将清理所有未使用的 Docker 资源！"
            read -p "确认清理？(yes/no): " confirm
            if [[ "$confirm" == "yes" ]]; then
                docker system prune -a -f --volumes
                print_success "所有未使用资源已清理"
            else
                print_info "操作已取消"
            fi
            ;;
        0) return ;;
        *) print_error "无效选择" ;;
    esac
}

# 主函数
main() {
    # 检查 Docker
    check_docker
    
    # 如果有参数，直接执行
    case "$1" in
        "start")
            quick_start
            exit 0
            ;;
        "build")
            build_all
            exit 0
            ;;
        "status")
            show_status
            exit 0
            ;;
        "logs")
            cd docker
            docker compose logs -f
            exit 0
            ;;
        "stop")
            cd docker
            docker compose down
            exit 0
            ;;
        "restart")
            cd docker
            docker compose restart
            exit 0
            ;;
    esac
    
    # 交互式菜单
    while true; do
        show_main_menu
        read -p "请选择 [0-8]: " choice
        
        case $choice in
            1) quick_start ;;
            2) handle_build_menu ;;
            3) handle_deploy_menu ;;
            4) show_status ;;
            5) show_logs ;;
            6) stop_services ;;
            7) restart_services ;;
            8) cleanup_resources ;;
            0)
                print_info "退出"
                exit 0
                ;;
            *)
                print_error "无效选择，请重新输入"
                sleep 1
                ;;
        esac
        
        # 暂停以便查看输出
        if [[ $choice != 0 ]]; then
            echo ""
            read -p "按回车键继续..." -r
        fi
    done
}

# 运行主函数
main "$@"

