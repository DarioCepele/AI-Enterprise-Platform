FROM node:22-alpine AS builder
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# NEXT_PUBLIC_* viene incorporato qui, in build: a runtime sarebbe troppo tardi.
# Il valore e' l'URL usato dal BROWSER, non dalla rete interna di compose:
# il nome di servizio "master-agent" non e' risolvibile dal browser.
ARG NEXT_PUBLIC_AGUI_URL=http://localhost:8000/agui
ENV NEXT_PUBLIC_AGUI_URL=$NEXT_PUBLIC_AGUI_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
