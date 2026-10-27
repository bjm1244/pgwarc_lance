ARG PG_MAJOR=16
ARG POSTGRES_BASE_IMAGE=postgres:${PG_MAJOR}
FROM ${POSTGRES_BASE_IMAGE}

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

# Build cargo-pgrx in the same GLIBC environment as the PostgreSQL target.
# A host-cached cargo-pgrx binary can require newer GLIBC symbols than this
# PostgreSQL image provides.
RUN cargo install --locked cargo-pgrx --version 0.19.1 \
    && cargo pgrx --version

ENV PGRX_HOME=/usr/local/pgrx
RUN cargo pgrx init "--pg${PG_MAJOR}" "$(which pg_config)"

RUN apt-get update && apt-get install -y --no-install-recommends \
        protobuf-compiler libprotobuf-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY Cargo.toml Cargo.lock ./
COPY vendor ./vendor
RUN mkdir -p src \
    && printf '::pgrx::pg_module_magic!(name, version);\n' > src/lib.rs \
    && cargo build --locked --release --no-default-features --features "pg${PG_MAJOR}"

COPY src ./src
COPY tests ./tests
COPY sql ./sql
COPY pgwarc_lance.control ./
# Build the official pgrx installation package from the source lockfile. pgrx
# embeds SQL entity metadata in the extension and generates the install SQL from
# that metadata; no previously generated install SQL is copied into this image.
RUN sha256sum Cargo.lock > /tmp/pgwarc-source-lock.sha256 \
    && cargo fetch --locked \
    && PGRX_BUILD_FLAGS="--locked --offline" CARGO_NET_OFFLINE=true cargo pgrx package \
        --pg-config "$(which pg_config)" \
        --out-dir "/build/target/release/pgwarc_lance-pg${PG_MAJOR}" \
        --no-default-features --features "pg${PG_MAJOR}" \
    && sha256sum -c /tmp/pgwarc-source-lock.sha256 \
    && install -D -m 755 "/build/target/release/pgwarc_lance-pg${PG_MAJOR}/usr/lib/postgresql/${PG_MAJOR}/lib/pgwarc_lance.so" "/usr/lib/postgresql/${PG_MAJOR}/lib/pgwarc_lance.so" \
    && install -D -m 644 "/build/target/release/pgwarc_lance-pg${PG_MAJOR}/usr/share/postgresql/${PG_MAJOR}/extension/pgwarc_lance.control" "/usr/share/postgresql/${PG_MAJOR}/extension/pgwarc_lance.control" \
    && cp -a "/build/target/release/pgwarc_lance-pg${PG_MAJOR}/usr/share/postgresql/${PG_MAJOR}/extension/." "/usr/share/postgresql/${PG_MAJOR}/extension/" \
    && extension_version="$(awk -F'\"' '/^version =/ {print $2; exit}' Cargo.toml)" \
    && test -s "/usr/share/postgresql/${PG_MAJOR}/extension/pgwarc_lance--${extension_version}.sql"

RUN if [ "${DEV_PERMISSIONS}" = "1" ]; then \
        chmod -R a+rwX /build /root /usr/local/pgrx \
        && chmod -R a+rwX /usr/lib/postgresql/${PG_MAJOR}/lib /usr/share/postgresql/${PG_MAJOR}/extension \
        && chmod a+rwX /usr/lib/postgresql/${PG_MAJOR} /usr/share/postgresql/${PG_MAJOR}; \
    fi
