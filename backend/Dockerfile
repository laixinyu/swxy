FROM python:3.11.7-slim

# 方案1：使用更稳定的镜像源（清华大学镜像源）
RUN echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list

# 或者使用官方源作为备用（如果镜像源有问题，请取消注释下面几行）
# RUN echo "deb http://deb.debian.org/debian bookworm main" > /etc/apt/sources.list && \
#     echo "deb http://security.debian.org/debian-security bookworm-security main" >> /etc/apt/sources.list && \
#     echo "deb http://deb.debian.org/debian bookworm-updates main" >> /etc/apt/sources.list

# 安装必要的构建工具和依赖，增加错误处理
RUN apt-get update --fix-missing || apt-get update && \
    apt-get install -y --no-install-recommends --fix-missing \
    build-essential \
    python3-dev \
    make \
    libgl1 \
    libgl1-mesa-glx \
    libglu1-mesa \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 更新 pip 并配置镜像源（阿里源）
RUN pip config set global.index-url "https://mirrors.aliyun.com/pypi/simple/" && \
    pip config set global.trusted-host "mirrors.aliyun.com" && \
    pip config set global.timeout 120
RUN pip install --upgrade pip setuptools wheel

# 设置工作目录。镜像内 /app 对应仓库里的 backend/app
WORKDIR /app

# 依赖文件在构建上下文根目录，先安装再拷贝代码
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r /tmp/requirements.txt

COPY app/ /app/

# 修改文件权限
RUN chmod -R 755 /app
RUN chmod +x /app/start.sh

# 通过 bash 启动。compose 会把 ./app 挂载到 /app，宿主文件是 644，直接 exec 会 permission denied。
ENTRYPOINT ["bash", "/app/start.sh"]
