# Base stage — shared by dev and production
FROM node:20-alpine AS base
WORKDIR /app/frontend
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile

# === Dev stage (default target — matches docker-compose.yml) ===
FROM base AS development
COPY . .
EXPOSE 3000
CMD ["yarn", "start"]

# === Production build stage ===
FROM base AS prod-build
ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL
ARG REACT_APP_GOOGLE_CLIENT_ID
ENV REACT_APP_GOOGLE_CLIENT_ID=$REACT_APP_GOOGLE_CLIENT_ID
COPY . .
RUN yarn build

# === Production stage — nginx serving static files ===
FROM nginx:alpine AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=prod-build /app/frontend/build /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
