FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps
COPY . .
# next.config.ts bakes the /api rewrite target at build time, so the backend URL is a build argument.
ARG BACKEND_URL=http://localhost:8010
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app ./
RUN addgroup -S mp && adduser -S mp -G mp && chown -R mp:mp /app/.next
USER mp
ENV PORT=3000
EXPOSE 3000
CMD ["sh", "-c", "npx next start -p ${PORT}"]
