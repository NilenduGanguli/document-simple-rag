# Pre-compiled frontend — no Node.js build stage required.
# Uses the pre-built dist/ output directly with nginx.
FROM nginx:1.25-alpine
COPY dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
