# Sample Cards Oracle APEX

Una aplicación de ejemplo para probar modificaciones con IA Genrativa.

## Integración continua

docker run -it --rm -v .:/app -w /app container-registry.oracle.com/database/sqlcl /nolog @validate.sql

docker run -it --rm -v .:/app -w /app container-registry.oracle.com/database/sqlcl /nolog @import.sql