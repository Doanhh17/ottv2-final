"""Official limits and compatibility with immutable jobs from earlier rules."""

RULES_VERSION = 6
SUPPORTED_RULES_VERSIONS = (1, 2, 3, 4, 5, 6)
RULES_HEADER = 'X-OTTV2-Rules-Version'
CPU_BUDGET_SECONDS = 7.0
RESPONSE_LIMIT_SECONDS = 2.5
MEMORY_LIMIT_MIB = 64
MAX_MOVES_PER_SIDE = 25
MAX_VALID_MOVES = 2 * MAX_MOVES_PER_SIDE

LEGACY_LIMITS = {'cpu_budget_seconds': 5.0, 'response_limit_seconds': 1.0,
                 'memory_limit_mib_per_bot': MEMORY_LIMIT_MIB}
PREVIOUS_LIMITS = {'cpu_budget_seconds': 30.0, 'response_limit_seconds': 2.0,
                   'memory_limit_mib_per_bot': MEMORY_LIMIT_MIB}
CAPPED_DRAW_LIMITS = {'cpu_budget_seconds': 10.0,
                  'response_limit_seconds': 2.0,
                  'memory_limit_mib_per_bot': MEMORY_LIMIT_MIB,
                  'max_valid_moves': 70}
MATERIAL_LIMITS = {**CAPPED_DRAW_LIMITS, 'move_limit_tiebreak': 'piece_count'}
RESPONSE_25_LIMITS = {**MATERIAL_LIMITS, 'response_limit_seconds': 2.5}
CURRENT_LIMITS = {**RESPONSE_25_LIMITS, 'cpu_budget_seconds': CPU_BUDGET_SECONDS,
                  'max_valid_moves': MAX_VALID_MOVES}
UPDATE_REQUIRED = 'Cần cập nhật máy chấm: 7 giây CPU/bot, 25 nước mỗi bên, phản hồi 2,5 giây/lượt.'


def supports_limits(limits, version=RULES_VERSION):
    if type(version) is not int or version not in SUPPORTED_RULES_VERSIONS:
        return False
    if (not isinstance(limits, dict)
            or any(type(value) not in (int, float) for key, value in limits.items()
                   if key != 'move_limit_tiebreak')
            or 'move_limit_tiebreak' in limits and type(limits['move_limit_tiebreak']) is not str):
        return False
    return (limits == LEGACY_LIMITS
            or version >= 2 and limits == PREVIOUS_LIMITS
            or version >= 3 and limits == CAPPED_DRAW_LIMITS
            or version >= 4 and limits == MATERIAL_LIMITS
            or version >= 5 and limits == RESPONSE_25_LIMITS
            or version >= 6 and limits == CURRENT_LIMITS)


def validate_move_limit(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 1000):
        raise ValueError('max_valid_moves must be an integer from 1 to 1000, or None for legacy games')
    return value


def whole_wall_limit(cpu_budget, max_valid_moves=None, response_timeout=RESPONSE_LIMIT_SECONDS):
    # Leaves room for both bots, free pondering, and scheduling on shared CPUs.
    # Include every turn's response deadline and startup; retain old profiles.
    return max(75, 8 * cpu_budget, (max_valid_moves or 0) * response_timeout + 15)
