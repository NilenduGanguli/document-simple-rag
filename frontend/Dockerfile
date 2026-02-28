# Stage 1: Build
FROM node:20-alpine AS build
WORKDIR /app

# Copy and source env defaults; Vite bakes VITE_* vars into the bundle at build time.
COPY set-env.sh .
RUN chmod +x set-env.sh

# Allow overriding VITE_DEFAULT_API_KEY via docker build --build-arg or
# by setting the variable in the host environment before sourcing set-env.sh.
ARG VITE_DEFAULT_API_KEY
ENV VITE_DEFAULT_API_KEY=${VITE_DEFAULT_API_KEY}

COPY package.json package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN . ./set-env.sh && npm run build

# Stage 2: Serve
FROM nginx:1.25-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
