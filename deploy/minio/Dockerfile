ARG BUILDER_IMAGE
ARG RUNTIME_IMAGE
FROM ${BUILDER_IMAGE} AS build
ARG SOURCE_COMMIT
ARG SOURCE_URL
ARG SOURCE_RELEASE
ARG TARGETARCH
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOOS=linux GOARCH=${TARGETARCH} GOFLAGS=-mod=readonly
WORKDIR /src
RUN test "$(go env GOVERSION)" = go1.24.8 \
    && git init \
    && git remote add origin "${SOURCE_URL}" \
    && git fetch --depth=1 origin "${SOURCE_COMMIT}" \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = "${SOURCE_COMMIT}"
RUN sha256sum go.sum > /tmp/go-sum.sha256 \
    && MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go > /tmp/minio-ldflags \
    && go build -trimpath -buildvcs=false -tags kqueue -ldflags="$(cat /tmp/minio-ldflags)" -o /out/minio . \
    && sha256sum -c /tmp/go-sum.sha256 \
    && git diff --exit-code -- go.mod go.sum \
    && cp LICENSE /out/LICENSE \
    && printf '%s\n%s\n%s\n' "${SOURCE_URL}" "${SOURCE_COMMIT}" "${SOURCE_RELEASE}" > /out/SOURCE \
    && mkdir /out/data && chown 65532:65532 /out/data
RUN printf '%s\n' 'package main' 'import ("net/http"; "os"; "time")' 'func main() { c := http.Client{Timeout: 5*time.Second}; r,e := c.Get("http://127.0.0.1:9000/minio/health/live"); if e != nil { os.Exit(1) }; defer r.Body.Close(); if r.StatusCode != 200 { os.Exit(1) } }' > /tmp/health.go \
    && go build -trimpath -buildvcs=false -ldflags='-s -w' -o /out/minio-health /tmp/health.go
FROM ${RUNTIME_IMAGE}
COPY --from=build /out/minio /minio
COPY --from=build /out/minio-health /minio-health
COPY --from=build /out/LICENSE /out/SOURCE /usr/share/minio/
COPY build-inputs.json /usr/share/minio/build-inputs.json
COPY --from=build --chown=65532:65532 /out/data /data
USER 65532:65532
EXPOSE 9000
ENTRYPOINT ["/minio"]
CMD ["server", "/data", "--address", ":9000"]
