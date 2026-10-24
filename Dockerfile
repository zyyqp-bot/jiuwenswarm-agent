# Placeholder image for validating the CloudBase Git build path.
#
# It exists for one reason: prove that a Git-triggered build, the service
# port mapping and the platform's default domain work from mainland China
# before the application image exists. Plan 2 Task 13 replaces this file
# with the multi-stage application image (Python 3.12 + the pinned upstream
# source fetched at build time + the built React SPA).
FROM nginx:alpine

RUN printf '%s\n' \
    '<!doctype html>' \
    '<html lang="zh"><head><meta charset="utf-8"><title>Workbench</title></head>' \
    '<body><h1>workbench placeholder</h1><p>CloudBase build path is working.</p></body></html>' \
    > /usr/share/nginx/html/index.html

EXPOSE 80
