FROM node:22-alpine AS build
WORKDIR /workspace
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/dashboard/package.json apps/dashboard/package.json
RUN pnpm install --frozen-lockfile --filter @sentinelqa/dashboard...
COPY apps/dashboard apps/dashboard
RUN pnpm --filter @sentinelqa/dashboard build

FROM nginx:1.27-alpine
ARG SENTINELQA_VERSION=0.1.0
ARG SENTINELQA_REVISION=unknown
ARG SENTINELQA_SOURCE=https://github.com/itsvsk/Qualestra
LABEL org.opencontainers.image.title="Qualestra dashboard" \
      org.opencontainers.image.description="Operator control room for Qualestra" \
      org.opencontainers.image.version="${SENTINELQA_VERSION}" \
      org.opencontainers.image.revision="${SENTINELQA_REVISION}" \
      org.opencontainers.image.source="${SENTINELQA_SOURCE}" \
      org.opencontainers.image.licenses="NOASSERTION"
COPY apps/dashboard/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/apps/dashboard/dist /usr/share/nginx/html
