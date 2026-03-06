#!/usr/bin/env bash
# Manual test script for example packs (#770)
#
# Run from the worktree root:
#   cd /Users/troy/dev/github/troylar/anteroom-770-pack-bugs-and-rules
#   source .venv/bin/activate
#   bash tests/manual/test_example_packs.sh

set -uo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

pass() { echo -e "  ${GREEN}✅ PASS${NC}: $1"; PASS=$((PASS+1)); }
fail() { echo -e "  ${RED}❌ FAIL${NC}: $1"; FAIL=$((FAIL+1)); }
warn() { echo -e "  ${YELLOW}⚠️  WARN${NC}: $1"; WARN=$((WARN+1)); }

PACKS_ROOT=$(python -c "from anteroom.services.starter_packs import get_built_in_pack_path; import pathlib; print(get_built_in_pack_path('code-review').parent)")

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  📦 Example Packs Test Script${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ──────────────────────────────────────────
# Test 1: Discovery API
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 1: Example pack discovery API${NC}"

EXAMPLE_COUNT=$(python -c "from anteroom.services.starter_packs import list_example_packs; print(len(list_example_packs()))")
if [ "$EXAMPLE_COUNT" = "3" ]; then
    pass "list_example_packs() returns 3 packs"
else
    fail "Expected 3 example packs, got $EXAMPLE_COUNT"
fi

ALL_COUNT=$(python -c "from anteroom.services.starter_packs import list_all_built_in_packs; print(len(list_all_built_in_packs()))")
if [ "$ALL_COUNT" = "5" ]; then
    pass "list_all_built_in_packs() returns 5 (2 starter + 3 example)"
else
    fail "Expected 5 total built-in packs, got $ALL_COUNT"
fi

echo ""

# ──────────────────────────────────────────
# Test 2: Path traversal prevention
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 2: get_built_in_pack_path security${NC}"

VALID=$(python -c "from anteroom.services.starter_packs import get_built_in_pack_path; print('found' if get_built_in_pack_path('code-review') else 'not found')")
if [ "$VALID" = "found" ]; then pass "Valid pack found"; else fail "Valid pack not found"; fi

TRAVERSAL=$(python -c "from anteroom.services.starter_packs import get_built_in_pack_path; print('blocked' if get_built_in_pack_path('../../etc') is None else 'LEAKED')")
if [ "$TRAVERSAL" = "blocked" ]; then pass "Path traversal blocked"; else fail "Path traversal NOT blocked!"; fi

MISSING=$(python -c "from anteroom.services.starter_packs import get_built_in_pack_path; print('none' if get_built_in_pack_path('nonexistent') is None else 'FOUND')")
if [ "$MISSING" = "none" ]; then pass "Nonexistent pack returns None"; else fail "Nonexistent pack returned a path"; fi

echo ""

# ──────────────────────────────────────────
# Test 3: Install example packs via CLI
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 3: Install example packs via aroom pack install${NC}"

for PACK_NAME in code-review writing-assistant strict-safety; do
    PACK_PATH="${PACKS_ROOT}/${PACK_NAME}"
    if [ ! -d "$PACK_PATH" ]; then
        fail "Pack directory not found: $PACK_PATH"
        continue
    fi

    OUTPUT=$(aroom pack install "$PACK_PATH" 2>&1) || true
    if echo "$OUTPUT" | grep -qi "installed\|updated"; then
        pass "Installed ${PACK_NAME}"
    else
        fail "Failed to install ${PACK_NAME}: $OUTPUT"
    fi
done

echo ""

# ──────────────────────────────────────────
# Test 4: Verify packs show in list
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 4: aroom pack list shows installed packs${NC}"

PACK_LIST=$(aroom pack list 2>&1) || true

for PACK_NAME in code-review writing-assistant strict-safety; do
    if echo "$PACK_LIST" | grep -q "$PACK_NAME"; then
        pass "${PACK_NAME} appears in pack list"
    else
        fail "${PACK_NAME} not found in pack list"
    fi
done

echo ""

# ──────────────────────────────────────────
# Test 5: Reinstall (duplicate prevention)
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 5: Reinstall shows 'Updated' not duplicate (#772)${NC}"

REINSTALL_OUTPUT=$(aroom pack install "${PACKS_ROOT}/code-review" 2>&1) || true
if echo "$REINSTALL_OUTPUT" | grep -qi "updated"; then
    pass "Reinstall shows 'Updated'"
elif echo "$REINSTALL_OUTPUT" | grep -qi "installed"; then
    warn "Reinstall shows 'Installed' (may have created duplicate)"
else
    fail "Unexpected reinstall output: $REINSTALL_OUTPUT"
fi

# Verify via pack list that code-review appears exactly once
DUP_COUNT=$(aroom pack list 2>&1 | grep -c "code-review" || true)
if [ "$DUP_COUNT" = "1" ]; then
    pass "No duplicate pack in list"
else
    fail "Found $DUP_COUNT entries for code-review in list (expected 1)"
fi

echo ""

# ──────────────────────────────────────────
# Test 6: Pack show details
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 6: aroom pack show details${NC}"

for REF in anteroom/code-review anteroom/writing-assistant anteroom/strict-safety; do
    SHOW_OUTPUT=$(aroom pack show "$REF" 2>&1) || true
    if echo "$SHOW_OUTPUT" | grep -q "1.0.0"; then
        pass "pack show ${REF} shows version"
    else
        fail "pack show ${REF} failed"
    fi
done

echo ""

# ──────────────────────────────────────────
# Test 7: Namespace resolution (pure Python)
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 7: Namespace-aware skill resolution${NC}"

python -c "
from anteroom.cli.skills import SkillRegistry, Skill

reg = SkillRegistry()
reg._skills = {
    'team-a/review': Skill(name='review', description='Team A', prompt='A', source='artifact:team', namespace='team-a'),
    'team-b/review': Skill(name='review', description='Team B', prompt='B', source='artifact:team', namespace='team-b'),
    'summarize': Skill(name='summarize', description='Summarize', prompt='S', source='artifact:team', namespace='anteroom'),
}
reg._rebuild_name_index()

results = []
results.append(('Ambiguous bare name returns None', reg.get('review') is None))
results.append(('Qualified namespace/name resolves', reg.get('team-a/review') is not None))
results.append(('Unique bare name resolves', reg.get('summarize') is not None))

descs = reg.get_skill_descriptions()
names = sorted([n for n, _ in descs])
results.append(('Display names qualified on collision', names == ['summarize', 'team-a/review', 'team-b/review']))

schema = reg.get_invoke_skill_definition()
enum_vals = sorted(schema['function']['parameters']['properties']['skill_name']['enum'])
results.append(('invoke_skill enum uses qualified names', enum_vals == ['summarize', 'team-a/review', 'team-b/review']))

for desc, passed in results:
    print(f'PASS:{desc}' if passed else f'FAIL:{desc}')
" | while IFS= read -r line; do
    case "$line" in
        PASS:*) pass "${line#PASS:}" ;;
        FAIL:*) fail "${line#FAIL:}" ;;
    esac
done

echo ""

# ──────────────────────────────────────────
# Test 8: Rule enforcer (pure Python)
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 8: Rule enforcer (hard enforcement)${NC}"

python -c "
from anteroom.services.artifacts import Artifact, ArtifactSource, ArtifactType
from anteroom.services.rule_enforcer import RuleEnforcer, parse_rule

art = Artifact(
    fqn='@test/rule/no-force-push', type=ArtifactType.RULE, namespace='test',
    name='no-force-push', content='No force pushing', source=ArtifactSource.TEAM,
    metadata={'enforce': 'hard', 'reason': 'Force pushing is prohibited',
              'matches': [{'tool': 'bash', 'pattern': r'git\s+push\s+--force'},
                          {'tool': 'bash', 'pattern': r'rm\s+-rf\s+/'}]})

enforcer = RuleEnforcer()
enforcer.load_rules([art])

results = []
results.append(('1 hard rule loaded', enforcer.rule_count == 1))

blocked, reason, _ = enforcer.check_tool_call('bash', {'command': 'git push --force origin main'})
results.append(('Force push blocked', blocked))
results.append(('Correct reason returned', reason == 'Force pushing is prohibited'))

blocked2, _, _ = enforcer.check_tool_call('bash', {'command': 'rm -rf /'})
results.append(('rm -rf / blocked', blocked2))

blocked3, _, _ = enforcer.check_tool_call('bash', {'command': 'git push origin main'})
results.append(('Safe push allowed', not blocked3))

blocked4, _, _ = enforcer.check_tool_call('read_file', {'path': '/tmp/test.txt'})
results.append(('Non-matching tool allowed', not blocked4))

soft_art = Artifact(fqn='@test/rule/soft', type=ArtifactType.RULE, namespace='test',
                    name='soft', content='Soft', source=ArtifactSource.TEAM,
                    metadata={'enforce': 'soft'})
results.append(('Soft rules not loaded as hard', parse_rule(soft_art) is None))

long_art = Artifact(fqn='@test/rule/long', type=ArtifactType.RULE, namespace='test',
                    name='long', content='Long', source=ArtifactSource.TEAM,
                    metadata={'enforce': 'hard', 'matches': [{'tool': 'bash', 'pattern': 'a' * 501}]})
results.append(('Pattern >500 chars rejected (ReDoS guard)', parse_rule(long_art) is None))

for desc, passed in results:
    print(f'PASS:{desc}' if passed else f'FAIL:{desc}')
" | while IFS= read -r line; do
    case "$line" in
        PASS:*) pass "${line#PASS:}" ;;
        FAIL:*) fail "${line#FAIL:}" ;;
    esac
done

echo ""

# ──────────────────────────────────────────
# Test 9: Cleanup — remove packs
# ──────────────────────────────────────────
echo -e "${BOLD}📋 Test 9: Cleanup — remove example packs${NC}"

for REF in anteroom/code-review anteroom/writing-assistant anteroom/strict-safety; do
    REMOVE_OUT=$(aroom pack remove "$REF" 2>&1) || true
    if echo "$REMOVE_OUT" | grep -qi "removed\|not found"; then
        pass "Removed ${REF}"
    else
        warn "Remove output: $REMOVE_OUT"
    fi
done

REMAINING=$(aroom pack list 2>&1 | grep -c "code-review\|writing-assistant\|strict-safety" || true)
if [ "$REMAINING" = "0" ]; then
    pass "All example packs removed"
else
    warn "$REMAINING example packs still in list"
fi

echo ""

# ──────────────────────────────────────────
# Summary
# ──────────────────────────────────────────
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [ "$FAIL" -eq 0 ]; then
    echo -e "  ${GREEN}✅ All tests passed${NC}: ${PASS} passed, ${WARN} warnings"
else
    echo -e "  ${RED}❌ ${FAIL} tests failed${NC}: ${PASS} passed, ${WARN} warnings"
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

exit $FAIL
