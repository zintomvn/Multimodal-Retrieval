FROM node:22-alpine

WORKDIR /app/apps/web

COPY apps/web/package.json ./
RUN npm install

COPY apps/web ./

CMD ["npm", "run", "dev", "--", "--port", "5173"]
