ARG PG_MAJOR=16
FROM postgres:${PG_MAJOR}

ARG PG_MAJOR=16
ARG DEV_PERMISSIONS=0
ARG RUST_TOOLCHAIN=1.96.0

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl build-essential pkg-config libssl-dev libclang-dev \
        ca-certificates git postgresql-server-dev-${PG_MAJOR} \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --default-toolchain "${RUST_TOOLCHAIN}" --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

RUN cargo install --locked cargo-pgrx --version 0.19.1

ENV PGRX_HOME=/usr/local/pgrx
RUN cargo pgrx init "--pg${PG_MAJOR}" "$(which pg_config)"

RUN apt-get update && apt-get install -y --no-install-recommends \
        protobuf-compiler libprotobuf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
RUN mkdir -p src \
    && printf '::pgrx::pg_module_magic!(name, version);\n' > src/lib.rs \
    && cargo build --locked --release --no-default-features --features "pg${PG_MAJOR}"

COPY src ./src
COPY tests ./tests
COPY sql ./sql
COPY pgwarc_lance.control ./
# cargo-pgrx 0.19.1 has no --locked install option. Do not resolve new network
# packages during installation, and verify that the source lockfile is unchanged.
RUN sha256sum Cargo.lock > /tmp/pgwarc-source-lock.sha256 \
    && CARGO_NET_OFFLINE=true cargo pgrx install --pg-config "$(which pg_config)" --release --no-default-features --features "pg${PG_MAJOR}" \
    && sha256sum -c /tmp/pgwarc-source-lock.sha256

RUN if [ "${DEV_PERMISSIONS}" = "1" ]; then \
        chmod -R a+rwX /build /root /usr/local/pgrx \
        && chmod -R a+rwX /usr/lib/postgresql/${PG_MAJOR}/lib /usr/share/postgresql/${PG_MAJOR}/extension \
        && chmod a+rwX /usr/lib/postgresql/${PG_MAJOR} /usr/share/postgresql/${PG_MAJOR}; \
    fi
