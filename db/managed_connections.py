"""Stable identities and scoped capabilities for extension-owned connections."""
from db.connection import transaction
from db.crud import query_one
from db.ai_connections import create_connection, update_connection


def metadata(connection_id):
    return query_one('SELECT * FROM managed_connections WHERE connection_id = $1', [connection_id])


def reconcile(owner, name, entries):
    """Atomically adopt legacy rows, reconcile entries, and retain missing IDs."""
    endpoint = f'extension://{owner}'
    result = {}
    with transaction() as con:
        legacy = con.execute(
            'SELECT id, model FROM ai_connections WHERE api_base_url = $1 AND id NOT IN (SELECT connection_id FROM managed_connections)',
            [endpoint],
        ).fetchall()
        for connection_id, model in legacy:
            if model and not con.execute('SELECT 1 FROM managed_connections WHERE owner = $1 AND local_key = $2', [owner, model]).fetchone():
                con.execute('INSERT INTO managed_connections (connection_id, owner, local_key) VALUES ($1, $2, $3)', [connection_id, owner, model])
        con.execute("UPDATE ai_connections SET name = name || ' [unavailable]' WHERE id IN (SELECT connection_id FROM managed_connections WHERE owner = $1) AND name NOT LIKE '% [unavailable]'", [owner])
        con.execute("UPDATE managed_connections SET available = 0, reason = 'Model is not available in the active account catalog.' WHERE owner = $1", [owner])
        for entry in entries:
            row = con.execute('SELECT connection_id FROM managed_connections WHERE owner = $1 AND local_key = $2', [owner, entry.key]).fetchone()
            fields = dict(name=f'{name} — {entry.name}', model=entry.model, thinking_levels=entry.thinking_levels)
            if row:
                connection_id = row[0]
                update_connection(connection_id, **fields)
            else:
                connection_id = create_connection(api_base_url=endpoint, **fields).id
                con.execute('INSERT INTO managed_connections (connection_id, owner, local_key) VALUES ($1, $2, $3)', [connection_id, owner, entry.key])
            con.execute('UPDATE managed_connections SET available = $1, reason = $2, images = $3, image_policy = $4 WHERE connection_id = $5', [entry.available, entry.reason, entry.images, entry.image_policy, connection_id])
            result[entry.key] = connection_id
    return result


def image_capability(connection):
    """Honor scoped policy; retain global flags for legacy provider contracts."""
    from db.model_capabilities import supports_images
    row = metadata(connection.id)
    if connection.api_base_url.startswith("extension://"):
        from core.extensions.registry import get_discovery, is_enabled
        owner = connection.api_base_url.removeprefix("extension://")
        if not is_enabled(owner):
            return False
    if row:
        return bool(row['available']) and (row['image_policy'] == 'enabled' or (row['image_policy'] == 'auto' and row['images'] == 'supported'))
    if connection.api_base_url.startswith('extension://'):
        entry = get_discovery().get(owner)
        if (
            entry is None
            or not entry.valid
            or entry.manifest.model_provider is None
            or entry.manifest.model_provider.scoped_capabilities
        ):
            return False
    return bool(connection.model) and supports_images(connection.model)


def agent_images(agent):
    from db.ai_connections import get_connection_by_id
    connection = get_connection_by_id(agent.connection_id) if agent.connection_id else None
    return connection is not None and image_capability(connection)


def routed_images(model, api_base):
    from db.ai_connections import list_connections
    from db.model_capabilities import supports_images
    if api_base and api_base.startswith("extension://"):
        matches = [c for c in list_connections() if c.api_base_url == api_base and c.model == model]
        return len(matches) == 1 and image_capability(matches[0])
    return supports_images(model)
