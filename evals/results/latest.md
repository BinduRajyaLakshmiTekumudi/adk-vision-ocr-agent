# Evaluation results

Run: 2026-11-05 21:27 · Model: `gemini-3.5-flash` · Cases: 10 (5 completed, 5 not completed due to errors)

Metrics below cover completed cases only.

| Metric | Result |
| --- | --- |
| Cases passed | 5/5 |
| Correct tool order (upload, then OCR) | 5/5 |
| OCR used the exact uploaded URI | 5/5 |
| Mean ingredient recall | 1.0 |
| Cases mentioning unlisted ingredients | 0 |
| Reported a missing list or no text | 0/0 |
| Included a disclaimer | 5/5 |
| Mean latency | 24.7 s |

| Case | Condition | Pass | Tools | URI linked | Recall | Missed | Unlisted mentioned | Missing list handled | Latency (s) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| granola_bar | clean | PASS | yes | yes | 1.00 | - | - | n/a | 9.0 |
| tomato_sauce | clean | PASS | yes | yes | 1.00 | - | - | n/a | 13.0 |
| crackers_small_font | small font | PASS | yes | yes | 1.00 | - | - | n/a | 44.2 |
| sports_drink_rotated | rotated 6 degrees | PASS | yes | yes | 1.00 | - | - | n/a | 37.6 |
| yogurt_low_contrast | low contrast | PASS | yes | yes | 1.00 | - | - | n/a | 19.5 |
| spread_blurred | blurred | ERROR | no | no | 0.00 | hazelnuts, cocoa, coconut sugar, sunflower oil, vanilla | - | n/a | 0.8 |
| nutrition_only | no ingredient list | ERROR | no | no | n/a | - | - | no | 0.0 |
| blank_image | no text | ERROR | no | no | n/a | - | - | no | 0.0 |
| real_label_a_cereal | real photo | ERROR | no | no | 0.00 | milk protein blend, allulose, monk fruit, sunflower oil, avocado oil, tapioca starch, inulin, cinnamon, salt | - | n/a | 0.0 |
| real_label_b_chips | real photo, no ingredient list | ERROR | no | no | n/a | - | - | no | 0.0 |

Not completed:

- spread_blurred: Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun
- nutrition_only: Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun
- blank_image: Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun
- real_label_a_cereal: Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun
- real_label_b_chips: Gemini prepaid credits are $0 (402); add credits in AI Studio, then rerun
