# docker/web/Dockerfile
# Build context: ./docker/web  (assets live alongside this Dockerfile)
# Serves a static placeholder HTML page on port 80.
# No Node, no React, no build step.
# Real frontend is implemented in F-055.

FROM nginx:1.27-alpine

COPY index.html /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf
