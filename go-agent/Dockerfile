FROM golang:1.23-alpine AS build
WORKDIR /src
COPY go.mod main.go ./
RUN go build -o /out/app .

FROM alpine:3.20
COPY --from=build /out/app /app
ENV PORT=8080
EXPOSE 8080
CMD ["/app"]
