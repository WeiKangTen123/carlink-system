FROM node:20-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1

# NEXT_PUBLIC_* vars must be present at build time -- Next.js inlines them
# into the client JS bundle here, not read at container runtime. Passing
# them only via `environment:` in docker-compose (as before) left the
# server-rendered HTML and the client bundle disagreeing on the value,
# which React's hydration then "resolved" by overwriting the server's
# correct URL with the client bundle's stale build-time default.
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_FILES_BASE_URL
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
ENV NEXT_PUBLIC_FILES_BASE_URL=${NEXT_PUBLIC_FILES_BASE_URL}

RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/package*.json ./
COPY --from=builder /app/node_modules ./node_modules

EXPOSE 3000

CMD ["npm", "run", "start"]
