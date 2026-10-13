FROM node:20-slim
WORKDIR /workspace/frontend
COPY package*.json ./
RUN npm ci
COPY src ./src
COPY public ./public
COPY next.config.mjs tailwind.config.ts postcss.config.mjs tsconfig.json ./
CMD ["npm", "run", "dev", "--", "--hostname", "0.0.0.0", "--port", "3000"]
