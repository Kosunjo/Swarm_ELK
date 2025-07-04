FROM nginx:latest

# index.html 파일을 Nginx의 기본 웹 루트 디렉토리로 복사
COPY index.html /usr/share/nginx/html

# Nginx가 /var/www/html을 사용하도록 설정 변경
#RUN mkdir -p /var/www/html && \
#    sed -i 's|/usr/share/nginx/html|/var/www/html|g' /etc/nginx/conf.d/default.conf
# 80 포트 노출
#EXPOSE 80

#CMD ["nginx", "-g", "daemon off;"]
