from typing import Optional, List, Dict, Sequence, Tuple, Any
from pathlib import Path

from ._rule import ResolvedRule
from ._const import ConstTypes
from ._exceptions import IsengardRunError, IsengardUnknownTargetError, IsengardConsistencyError
from ._target import TargetHandlersBundle, BaseTargetHandler
from ._db import DB


class Runner:
    def __init__(self, rules: Dict[str, ResolvedRule], config: Dict[str, ConstTypes], target_handlers: TargetHandlersBundle, db_path: Path):
        self.target_to_rule = {
            output: rule for rule in rules.values() for output in rule.resolved_outputs
        }
        self.rules = rules
        self.config = config
        self.target_handlers = target_handlers
        self.db_path = db_path

    def clean(self, target: str) -> None:
        try:
            rule = self.target_to_rule[target]
        except KeyError:
            raise IsengardUnknownTargetError(f"No rule has target `{target}` as output")

        already_cleaned = set()

        def _clean(rule: ResolvedRule, parent_rules: Sequence[ResolvedRule]) -> None:
            if rule in already_cleaned:
                return
            already_cleaned.add(rule)

            for target in rule.resolved_outputs:
                previous_fingerprint = db.fetch_previous_fingerprint(target)
                cooked, handler = self.target_handlers.cook_target(target, previous_fingerprint)
                handler.clean(cooked)

            for target in rule.resolved_inputs:
                sub_parent_rules = [*parent_rules, rule]

                try:
                    sub_rule = self.target_to_rule[target]
                except KeyError:
                    raise IsengardUnknownTargetError(f"No rule has target `{target}` as output (needed by {'->'.join(r.id for r in sub_parent_rules)}")

                if sub_rule in sub_parent_rules:
                    raise IsengardConsistencyError(
                        f"Recursion detection in rules {'->'.join(r.id for r in sub_parent_rules)}"
                    )

                _clean(sub_rule, sub_parent_rules)

        with DB.connect(self.db_path) as db:
            _clean(rule, [])

    def run(self, target: str) -> bool:
        # {<target>: (<cooked>, <handler>, <has_changed>)}
        targets_eval_cache: Dict[str, Tuple[Any, BaseTargetHandler, bool]] = {}

        def _run(target: str, parent_rule: Optional[ResolvedRule]) -> Tuple[Any, BaseTargetHandler, bool]:
            # 0) Fast track if the target's rule has already been evaluated
            try:
                return targets_eval_cache[target]
            except KeyError:
                pass

            # 1) Retreive rule
            try:
                rule = self.target_to_rule[target]
            except KeyError:
                # Target has not been generated by a rule
                previous_fingerprint = db.fetch_previous_fingerprint(target)
                cooked, handler = self.target_handlers.cook_target(target, previous_fingerprint)

                if not handler.ALLOW_NON_RULE_GENERATED_TARGET:
                    extra = f" (needed by rule `{parent_rule.id}`)" if parent_rule else ""
                    raise IsengardConsistencyError(
                        f"No rule has target `{target!r}` as output{extra}"
                    )
                else:
                    # The target must be a prerequisit existing on disk
                    if previous_fingerprint is not None:
                        has_changed = handler.need_rebuild(cooked, previous_fingerprint)
                    else:
                        has_changed = True
                    targets_eval_cache[target] = (cooked, handler, has_changed)
                    return (cooked, handler, has_changed)

            rebuild_needed = False
            inputs: List[Any] = []

            # 2) Evaluate each input
            for input_target in rule.resolved_inputs:
                input_cooked, _, input_has_changed = _run(input_target, rule)
                rebuild_needed |= input_has_changed
                inputs.append(input_cooked)

            # 3) Evaluate the outputs
            outputs: List[Any] = []
            for output_target in rule.resolved_outputs:
                output_previous_fingerprint = db.fetch_previous_fingerprint(output_target)
                output_cooked, output_handler = self.target_handlers.cook_target(output_target, output_previous_fingerprint)
                if output_previous_fingerprint is not None:
                    rebuild_needed |= handler.need_rebuild(output_cooked, output_previous_fingerprint)
                else:
                    rebuild_needed = True
                outputs.append((output_target, output_cooked, output_handler))

            # 4) Actually run the rule if needed
            if rebuild_needed:
                print(f"> {rule.id}")
                try:
                    rule.run([output_cooked for _, output_cooked, _ in outputs], inputs, self.config)
                except Exception as exc:
                    raise IsengardRunError(f"Error in rule `{rule.id}`: {exc}") from exc

            # 5) Update the build cache
            for output_target, output_cooked, output_handler in outputs:
                targets_eval_cache[output_target] = (output_cooked, output_handler, rebuild_needed)

            return targets_eval_cache[target]

        with DB.connect(self.db_path) as db:
            _, _, has_changed = _run(target, None)
            return has_changed
