#!/bin/bash
# =====================================================
# AntCode Docker 快速启动脚本
# =====================================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
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
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}"
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

# 读取 .env 变量（如果存在）
get_env_var() {
    local key="$1"
    local default_value="$2"
    local env_file="../.env"

    if [[ -f "$env_file" ]]; then
        local value
        value=$(grep -E "^${key}=" "$env_file" | tail -n1 | cut -d '=' -f2- | tr -d '\r' | tr -d '"')
        if [[ -n "$value" ]]; then
            echo "$value"
            return
        fi
    fi

    echo "$default_value"
}

# 显示菜单
show_menu() {
    print_header "AntCode Docker 部署选择"
    echo "请选择部署配置："
    echo ""
    echo "  1) SQLite + 内存缓存（默认，最简单）"
    echo "  2) SQLite + Redis"
    echo "  3) MySQL + Redis"
    echo "  4) PostgreSQL + Redis"
    echo "  5) 自定义配置"
    echo "  0) 退出"
    echo ""
}

# SQLite + 内存缓存
deploy_sqlite_memory() {
    print_header "部署：SQLite + 内存缓存"
    print_info "这是最简单的配置，无需额外服务"
    
    cd docker
    docker-compose up -d
    
    print_success "部署完成！"
    show_access_info
}

# SQLite + Redis
deploy_sqlite_redis() {
    print_header "部署：SQLite + Redis"
    
    # 检查 docker-compose.yml 中 Redis 是否已启用
    if grep -q "^  redis:" docker/docker-compose.yml 2>/dev/null; then
        print_warning "Redis 服务已经在 docker-compose.yml 中启用"
    else
        print_info "请手动编辑 docker/docker-compose.yml，取消注释 redis 服务"
        print_info "然后修改 antcode-api 的 REDIS_URL 配置"
        print_warning "按任意键继续..."
        read -n 1 -s
    fi
    
    cd docker
    docker-compose up -d
    
    print_success "部署完成！"
    show_access_info
}

# MySQL + Redis
deploy_mysql_redis() {
    print_header "部署：MySQL + Redis"
    
    print_warning "需要修改 docker/docker-compose.yml："
    echo "  1. 取消注释 mysql 和 redis 服务"
    echo "  2. 修改 antcode-api 的 DATABASE_URL 为 MySQL"
    echo "  3. 修改 antcode-api 的 build.args.DB_TYPE 为 mysql"
    echo "  4. 修改 antcode-api 的 REDIS_URL"
    echo ""
    print_info "是否已完成修改？(y/n)"
    read -r answer
    
    if [[ "$answer" != "y" ]]; then
        print_warning "请先修改配置文件"
        return
    fi
    
    cd docker
    docker-compose up -d --build
    
    print_success "部署完成！"
    show_access_info
}

# PostgreSQL + Redis
deploy_postgres_redis() {
    print_header "部署：PostgreSQL + Redis"
    
    print_warning "需要修改 docker/docker-compose.yml："
    echo "  1. 取消注释 postgres 和 redis 服务"
    echo "  2. 修改 antcode-api 的 DATABASE_URL 为 PostgreSQL"
    echo "  3. 修改 antcode-api 的 build.args.DB_TYPE 为 postgres"
    echo "  4. 修改 antcode-api 的 REDIS_URL"
    echo ""
    print_info "是否已完成修改？(y/n)"
    read -r answer
    
    if [[ "$answer" != "y" ]]; then
        print_warning "请先修改配置文件"
        return
    fi
    
    cd docker
    docker-compose up -d --build
    
    print_success "部署完成！"
    show_access_info
}

# 显示访问信息
show_access_info() {
    echo ""
    print_header "访问信息"
    local api_port
    local web_port
    api_port=$(get_env_var "SERVER_PORT" "8000")
    web_port=$(get_env_var "FRONTEND_PORT" "3000")
    echo "  🌐 API 地址: http://localhost:${api_port}"
    echo "  📚 API 文档: http://localhost:${api_port}/docs"
    echo "  💻 Web 控制台: http://localhost:${web_port}"
    echo "  👤 默认账号: admin / admin"
    echo ""
    print_info "查看日志: docker-compose logs -f"
    print_info "停止服务: docker-compose down"
    echo ""
}

# 查看状态
show_status() {
    print_header "服务状态"
    cd docker
    docker-compose ps
    echo ""
    print_info "查看详细日志: docker-compose logs -f [service-name]"
}

# 停止服务
stop_services() {
    print_header "停止服务"
    print_warning "这将停止所有容器但保留数据"
    print_info "是否继续？(y/n)"
    read -r answer
    
    if [[ "$answer" == "y" ]]; then
        cd docker
        docker-compose down
        print_success "服务已停止"
    else
        print_info "操作已取消"
    fi
}

# 主函数
main() {
    # 检查 Docker
    check_docker
    
    # 如果有参数，直接执行
    case "$1" in
        "status")
            show_status
            exit 0
            ;;
        "stop")
            stop_services
            exit 0
            ;;
        "logs")
            cd docker
            docker-compose logs -f
            exit 0
            ;;
    esac
    
    # 交互式菜单
    while true; do
        show_menu
        read -p "请选择 [0-5]: " choice
        
        case $choice in
            1)
                deploy_sqlite_memory
                break
                ;;
            2)
                deploy_sqlite_redis
                break
                ;;
            3)
                deploy_mysql_redis
                break
                ;;
            4)
                deploy_postgres_redis
                break
                ;;
            5)
                print_info "请手动编辑 docker/docker-compose.yml 和 docker/Dockerfile"
                print_info "完成后运行: cd docker && docker-compose up -d --build"
                break
                ;;
            0)
                print_info "退出"
                exit 0
                ;;
            *)
                print_error "无效选择，请重新输入"
                sleep 1
                ;;
        esac
    done
}

# 运行主函数
main "$@"

