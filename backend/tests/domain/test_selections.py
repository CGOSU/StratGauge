import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from stratgauge.domain.selections import (
    SELECTION_ANNOTATION_TAXONOMY_VERSION,
    SelectionAnnotation,
    SelectionAnnotationDecision,
    SelectionAnnotationOutlook,
    SelectionReusableTag,
    SelectionReusableTagCategory,
    SelectionReusableTagEvent,
    SelectionReusableTagEventType,
    SelectionReusableTagSnapshot,
    SelectionReusableTagView,
)

TAG_ID = "annotation-tag-0123456789abcdef0123"
EVENT_ID = "annotation-tag-event-abcdef0123456789abcd"
CREATED_AT = datetime(2026, 1, 5, 9, tzinfo=UTC)


class SelectionAnnotationTagContractTests(unittest.TestCase):
    def test_legacy_annotation_json_remains_readable_without_migration(self) -> None:
        annotation = SelectionAnnotation.model_validate(
            {
                "annotation_id": "annotation-legacy",
                "candidate_id": "batch-1:000001.SZ",
                "reviewed_at": "2026-01-05T09:00:00+00:00",
                "recorded_at": "2026-01-05T09:00:01+00:00",
                "overall_score": 3,
                "outlook": "neutral",
                "decision": "watch",
                "position_tags": ["support"],
                "pattern_tags": ["pullback"],
                "price_volume_tags": [],
                "sector_tags": [],
                "taxonomy_version": SELECTION_ANNOTATION_TAXONOMY_VERSION,
                "note": "旧记录",
            }
        )

        self.assertEqual(annotation.position_tags[0].value, "support")
        self.assertEqual(annotation.reusable_tag_snapshots, ())

    def test_reusable_tag_snapshot_preserves_historical_label_and_category(self) -> None:
        snapshot = SelectionReusableTagSnapshot(
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.PATTERN,
            label="缩量企稳",
        )
        annotation = SelectionAnnotation(
            annotation_id="annotation-with-tag",
            candidate_id="batch-1:000001.SZ",
            reviewed_at=CREATED_AT,
            recorded_at=datetime(2026, 1, 5, 9, 1, tzinfo=UTC),
            overall_score=4,
            outlook=SelectionAnnotationOutlook.BULLISH,
            decision=SelectionAnnotationDecision.PREPARE_BUY,
            reusable_tag_snapshots=(snapshot,),
        )

        self.assertEqual(annotation.reusable_tag_snapshots, (snapshot,))
        self.assertEqual(annotation.reusable_tag_snapshots[0].label, "缩量企稳")

    def test_reusable_tag_rejects_invalid_id_label_and_naive_time(self) -> None:
        with self.assertRaises(ValidationError):
            SelectionReusableTag(
                tag_id="tag-invalid",
                category=SelectionReusableTagCategory.POSITION,
                label="低位",
                created_at=CREATED_AT,
            )
        with self.assertRaises(ValidationError):
            SelectionReusableTag(
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.POSITION,
                label=" ",
                created_at=CREATED_AT,
            )
        with self.assertRaises(ValidationError):
            SelectionReusableTag(
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.POSITION,
                label="低位",
                created_at=datetime(2026, 1, 5, 9),
            )

    def test_rename_event_requires_new_label_and_cannot_change_category(self) -> None:
        with self.assertRaises(ValidationError):
            SelectionReusableTagEvent(
                event_id=EVENT_ID,
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.PATTERN,
                event_type=SelectionReusableTagEventType.RENAME,
                occurred_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
            )

        definition = SelectionReusableTag(
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.PATTERN,
            label="回踩",
            created_at=CREATED_AT,
        )
        event = SelectionReusableTagEvent(
            event_id=EVENT_ID,
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.POSITION,
            event_type=SelectionReusableTagEventType.RENAME,
            occurred_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
            new_label="缩量回踩",
        )

        with self.assertRaisesRegex(ValueError, "category"):
            SelectionReusableTagView.project(definition, (event,))

    def test_tag_projection_applies_append_only_rename_archive_restore(self) -> None:
        definition = SelectionReusableTag(
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.POSITION,
            label="低位",
            created_at=CREATED_AT,
        )
        events = (
            SelectionReusableTagEvent(
                event_id=EVENT_ID,
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.POSITION,
                event_type=SelectionReusableTagEventType.RENAME,
                occurred_at=datetime(2026, 1, 5, 10, tzinfo=UTC),
                new_label="低位支撑",
            ),
            SelectionReusableTagEvent(
                event_id="annotation-tag-event-1234567890abcdef1234",
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.POSITION,
                event_type=SelectionReusableTagEventType.ARCHIVE,
                occurred_at=datetime(2026, 1, 5, 11, tzinfo=UTC),
            ),
            SelectionReusableTagEvent(
                event_id="annotation-tag-event-234567890abcdef12345",
                tag_id=TAG_ID,
                category=SelectionReusableTagCategory.POSITION,
                event_type=SelectionReusableTagEventType.RESTORE,
                occurred_at=datetime(2026, 1, 5, 12, tzinfo=UTC),
            ),
        )

        projected = SelectionReusableTagView.project(definition, events)

        self.assertEqual(projected.label, "低位支撑")
        self.assertTrue(projected.active)
        self.assertEqual(projected.category, SelectionReusableTagCategory.POSITION)
        self.assertEqual(projected.events, events)

    def test_tag_projection_rejects_non_monotonic_events(self) -> None:
        definition = SelectionReusableTag(
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.POSITION,
            label="低位",
            created_at=CREATED_AT,
        )
        event = SelectionReusableTagEvent(
            event_id=EVENT_ID,
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.POSITION,
            event_type=SelectionReusableTagEventType.ARCHIVE,
            occurred_at=CREATED_AT,
        )

        with self.assertRaisesRegex(ValueError, "after"):
            SelectionReusableTagView.project(definition, (event,))

    def test_annotation_rejects_duplicate_reusable_tag_ids(self) -> None:
        snapshot = SelectionReusableTagSnapshot(
            tag_id=TAG_ID,
            category=SelectionReusableTagCategory.PATTERN,
            label="回踩",
        )

        with self.assertRaisesRegex(ValidationError, "duplicates"):
            SelectionAnnotation(
                annotation_id="annotation-duplicate-tags",
                candidate_id="batch-1:000001.SZ",
                reviewed_at=CREATED_AT,
                recorded_at=datetime(2026, 1, 5, 9, 1, tzinfo=UTC),
                overall_score=3,
                outlook=SelectionAnnotationOutlook.NEUTRAL,
                decision=SelectionAnnotationDecision.WATCH,
                reusable_tag_snapshots=(snapshot, snapshot),
            )


if __name__ == "__main__":
    unittest.main()
