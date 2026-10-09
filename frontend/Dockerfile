# syntax=docker/dockerfile:1
FROM node:22-alpine AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH
RUN corepack enable
WORKDIR /srv
COPY package.json ./
RUN pnpm install --frozen-lockfile=false

# ---- development: hot reload, source is bind-mounted by docker compose ----
FROM base AS dev
ENV NODE_ENV=development
COPY . .
EXPOSE 3000
CMD ["pnpm", "exec", "next", "dev", "-p", "3000", "-H", "0.0.0.0"]

# ---- production build ----
FROM base AS build
ENV NODE_ENV=production
COPY . .
RUN pnpm exec next build

FROM node:22-alpine AS prod
WORKDIR /srv
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /srv/public ./public
COPY --from=build /srv/.next/standalone ./
COPY --from=build /srv/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
