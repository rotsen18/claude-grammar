from dataclasses import dataclass, field


@dataclass
class Correction:
    original: str
    replacement: str
    rule: str
    offset: int
    length: int
    message: str
    category: str = ""


@dataclass
class CorrectionResult:
    original_text: str
    corrected_text: str
    corrections: list[Correction] = field(default_factory=list)
    corrector_name: str = ""


class BaseCorrector:
    name: str = "base"

    def correct(self, text: str) -> CorrectionResult:
        raise NotImplementedError
