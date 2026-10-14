FROM python:3.12-alpine

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Alpine 换阿里云源
RUN sed -i 's#dl-cdn.alpinelinux.org#mirrors.aliyun.com#g' /etc/apk/repositories

RUN apk add --no-cache \
        bash \
        git \
        curl \
        github-cli \
        openssh-client \
        tzdata \
        unzip \
        zip \
        rsync \
 && git config --global --add safe.directory '*' \
 && git config --global init.defaultBranch main

WORKDIR /app

COPY requirements.txt .

# pip 换清华源
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
 && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn \
 && pip install --no-cache-dir -r requirements.txt

COPY app/ /app/app/

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "--threads", "8", "--timeout", "600", "app.main:app"]
