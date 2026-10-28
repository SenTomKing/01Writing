FROM node:24-alpine@sha256:06d7adf3e0ef61a13f1d28682726366f97a15aa1888c934d3ecec86c33918b53 AS build
WORKDIR /web
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm install --global pnpm@10.20.0 --registry="$NPM_REGISTRY"
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm/store pnpm install --frozen-lockfile --registry="$NPM_REGISTRY" --store-dir=/pnpm/store --network-concurrency=2 --fetch-retries=1
COPY frontend ./
RUN pnpm build
FROM nginx:stable-alpine@sha256:47928bf67eb62a1863cb08d9ecb52d3e704304ba614eaee17884204677702951
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
