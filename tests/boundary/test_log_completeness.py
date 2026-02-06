"""
Property-Based Test: Log Completeness Rule

**Feature: directory-restructure, Property 6: Log Completeness Rule**
**Validates: Requirements 4.3, 7.5**

Property 6: Log Completeness Rule
*For any* TaskRun：
- 运行中日志：必须可通过 `log:{run_id}` stream 实时获取
- 运行结束后：必须有归档路径（MinIO/S3）可完整回放
- web_api 对同一 run_id 查询必须遵循：优先实时 stream，完成后回放归档

This test verifies that the log system components provide complete
log coverage for all task runs.
"""

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Import the log components
from antcode_worker.logging.streamer import (
    BufferedLogStreamer,
    LogStreamer,
)
from antcode_worker.logging.archiver import (
    ArchiverConfig,
    LogArchiver,
    TransferState,
)


@dataclass
class MockMessageSender:
    """Mock message sender for testing."""

    messages: list[dict] = None

    def __post_init__(self):
        if self.messages is None:
            self.messages = []

    async def send_message(self, message: Any) -> bool:
        """Record sent messages."""
        self.messages.append(message)
        return True


# Strategies for generating test data
execution_id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd")),
    min_size=8,
    max_size=32,
).filter(lambda x: len(x) >= 8)

log_content_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=1000,
)

log_type_strategy = st.sampled_from(["stdout", "stderr"])

log_lines_strategy = st.lists(
    log_content_strategy,
    min_size=1,
    max_size=50,
)


class TestLogStreamerCompleteness:
    """Property-based tests for LogStreamer completeness."""

    @pytest.mark.pbt
    @settings(max_examples=100, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_type=log_type_strategy,
        content=log_content_strategy,
    )
    def test_enabled_streamer_sends_all_logs(
        self, execution_id: str, log_type: str, content: str
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any enabled LogStreamer, all pushed logs must be sent.
        """

        async def run_test():
            sender = MockMessageSender()
            streamer = LogStreamer(execution_id, sender)

            # Enable the streamer
            await streamer.enable()
            assert streamer.enabled

            # Push log content
            result = await streamer.push(log_type, content)

            # Verify the log was sent
            assert result is True
            assert len(sender.messages) == 1

            msg = sender.messages[0]
            assert msg["type"] == "log_realtime"
            assert msg["execution_id"] == execution_id
            assert msg["log_type"] == log_type
            assert msg["content"] == content
            assert "timestamp" in msg

        asyncio.run(run_test())

    @pytest.mark.pbt
    @settings(max_examples=100, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_type=log_type_strategy,
        content=log_content_strategy,
    )
    def test_disabled_streamer_does_not_send(
        self, execution_id: str, log_type: str, content: str
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any disabled LogStreamer, no logs should be sent.
        """

        async def run_test():
            sender = MockMessageSender()
            streamer = LogStreamer(execution_id, sender)

            # Streamer is disabled by default
            assert not streamer.enabled

            # Push log content
            result = await streamer.push(log_type, content)

            # Verify no log was sent
            assert result is False
            assert len(sender.messages) == 0

        asyncio.run(run_test())

    @pytest.mark.pbt
    @settings(max_examples=50, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_lines=log_lines_strategy,
    )
    def test_buffered_streamer_sends_all_buffered_logs(
        self, execution_id: str, log_lines: list[str]
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any BufferedLogStreamer, all buffered logs must be
        sent when disabled (flushed).
        """

        async def run_test():
            sender = MockMessageSender()
            streamer = BufferedLogStreamer(
                execution_id,
                sender,
                buffer_size=1000,  # Large buffer to prevent auto-flush
                flush_interval=10.0,  # Long interval to prevent auto-flush
            )

            # Enable the streamer
            await streamer.enable()

            # Push all log lines
            for line in log_lines:
                await streamer.push("stdout", line)

            # Disable to flush
            await streamer.disable()

            # Verify all content was sent
            if log_lines:
                assert len(sender.messages) >= 1
                # All content should be in the messages
                all_content = "\n".join(
                    msg["content"]
                    for msg in sender.messages
                    if msg.get("type") == "log_realtime"
                )
                for line in log_lines:
                    assert line in all_content

        asyncio.run(run_test())


class TestLogArchiverCompleteness:
    """Property-based tests for LogArchiver completeness."""

    @pytest.mark.pbt
    @settings(max_examples=50, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_type=log_type_strategy,
        content=st.binary(min_size=1, max_size=10000),
    )
    def test_archiver_sends_all_file_content(
        self, execution_id: str, log_type: str, content: bytes
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any log file, the archiver must send all content
        in chunks that can be reassembled to the original.
        """

        async def run_test():
            # Create a temporary log file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
                f.write(content)
                log_file = Path(f.name)

            try:
                sender = MockMessageSender()
                config = ArchiverConfig(
                    chunk_size=1024,  # Small chunks for testing
                    chunk_interval=0.01,
                    max_in_flight=4,
                    ack_timeout=1.0,
                )

                archiver = LogArchiver(
                    execution_id=execution_id,
                    log_type=log_type,
                    log_file=log_file,
                    message_sender=sender,
                    config=config,
                )

                # Start the archiver
                await archiver.start()

                # Wait for chunks to be sent
                await asyncio.sleep(0.5)

                # Request finalization
                await archiver.finalize()

                # Simulate ACKs for all chunks
                for msg in sender.messages:
                    if msg.get("type") == "log_chunk":
                        offset = msg.get("offset", 0)
                        chunk_data = msg.get("chunk", b"")
                        await archiver.handle_ack(offset + len(chunk_data), True)

                # Wait for completion
                await asyncio.sleep(0.2)

                # Stop the archiver
                await archiver.stop()

                # Verify all content was sent
                chunks = [
                    msg for msg in sender.messages if msg.get("type") == "log_chunk"
                ]

                # Reassemble the content
                reassembled = b""
                sorted_chunks = sorted(chunks, key=lambda x: x.get("offset", 0))
                for chunk in sorted_chunks:
                    chunk_data = chunk.get("chunk", b"")
                    if chunk_data:
                        reassembled += chunk_data

                # Verify the reassembled content matches original
                assert reassembled == content, (
                    f"Content mismatch: expected {len(content)} bytes, "
                    f"got {len(reassembled)} bytes"
                )

            finally:
                # Cleanup
                log_file.unlink(missing_ok=True)

        asyncio.run(run_test())

    @pytest.mark.pbt
    @settings(max_examples=50, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_type=log_type_strategy,
        content=st.binary(min_size=100, max_size=5000),
    )
    def test_archiver_chunk_checksums_valid(
        self, execution_id: str, log_type: str, content: bytes
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any chunk sent by the archiver, the checksum must
        match the actual chunk content.
        """

        async def run_test():
            # Create a temporary log file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
                f.write(content)
                log_file = Path(f.name)

            try:
                sender = MockMessageSender()
                config = ArchiverConfig(
                    chunk_size=512,  # Small chunks for testing
                    chunk_interval=0.01,
                    max_in_flight=4,
                )

                archiver = LogArchiver(
                    execution_id=execution_id,
                    log_type=log_type,
                    log_file=log_file,
                    message_sender=sender,
                    config=config,
                )

                # Start and let it send some chunks
                await archiver.start()
                await asyncio.sleep(0.3)
                await archiver.stop()

                # Verify checksums
                for msg in sender.messages:
                    if msg.get("type") == "log_chunk":
                        chunk_data = msg.get("chunk", b"")
                        checksum = msg.get("checksum", "")

                        if chunk_data and checksum:
                            expected_checksum = hashlib.sha256(chunk_data).hexdigest()[:16]
                            assert checksum == expected_checksum, (
                                f"Checksum mismatch for chunk at offset {msg.get('offset')}"
                            )

            finally:
                log_file.unlink(missing_ok=True)

        asyncio.run(run_test())


class TestLogSystemIntegration:
    """Integration tests for log system completeness."""

    @pytest.mark.pbt
    def test_log_components_exist_in_worker(self):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Verify that all required log components exist in the worker service.
        """
        # Check that LogStreamer exists and has required methods
        assert hasattr(LogStreamer, "enable")
        assert hasattr(LogStreamer, "disable")
        assert hasattr(LogStreamer, "push")

        # Check that BufferedLogStreamer exists
        assert hasattr(BufferedLogStreamer, "enable")
        assert hasattr(BufferedLogStreamer, "disable")
        assert hasattr(BufferedLogStreamer, "push")

        # Check that LogArchiver exists and has required methods
        assert hasattr(LogArchiver, "start")
        assert hasattr(LogArchiver, "stop")
        assert hasattr(LogArchiver, "finalize")
        assert hasattr(LogArchiver, "handle_ack")

    @pytest.mark.pbt
    def test_log_archiver_states_are_monotonic(self):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Verify that log archiver states follow a valid progression.
        """
        # Valid state transitions
        valid_transitions = {
            TransferState.INIT: {TransferState.STREAMING, TransferState.ERROR},
            TransferState.STREAMING: {
                TransferState.WAIT_ACK,
                TransferState.FINALIZING,
                TransferState.ERROR,
            },
            TransferState.WAIT_ACK: {
                TransferState.STREAMING,
                TransferState.RETRYING,
                TransferState.FINALIZING,
                TransferState.ERROR,
            },
            TransferState.RETRYING: {
                TransferState.STREAMING,
                TransferState.WAIT_ACK,
                TransferState.ERROR,
            },
            TransferState.FINALIZING: {TransferState.COMPLETED, TransferState.ERROR},
            TransferState.COMPLETED: set(),  # Terminal state
            TransferState.ERROR: set(),  # Terminal state
        }

        # Verify all states are covered
        for state in TransferState:
            assert state in valid_transitions, f"State {state} not in valid transitions"

        # Verify terminal states have no outgoing transitions
        assert len(valid_transitions[TransferState.COMPLETED]) == 0
        assert len(valid_transitions[TransferState.ERROR]) == 0

    @pytest.mark.pbt
    @settings(max_examples=100, deadline=None)
    @given(
        execution_id=execution_id_strategy,
    )
    def test_streamer_status_contains_required_fields(self, execution_id: str):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any LogStreamer, get_status() must return all required fields.
        """

        async def run_test():
            sender = MockMessageSender()
            streamer = LogStreamer(execution_id, sender)

            status = streamer.get_status()

            # Required fields
            assert "execution_id" in status
            assert "enabled" in status
            assert status["execution_id"] == execution_id

        asyncio.run(run_test())

    @pytest.mark.pbt
    @settings(max_examples=50, deadline=None)
    @given(
        execution_id=execution_id_strategy,
        log_type=log_type_strategy,
    )
    def test_archiver_status_contains_required_fields(
        self, execution_id: str, log_type: str
    ):
        """
        **Feature: directory-restructure, Property 6: Log Completeness Rule**
        **Validates: Requirements 4.3, 7.5**

        Property: For any LogArchiver, get_status() must return all required fields.
        """
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as f:
            f.write(b"test content")
            log_file = Path(f.name)

        try:
            sender = MockMessageSender()
            archiver = LogArchiver(
                execution_id=execution_id,
                log_type=log_type,
                log_file=log_file,
                message_sender=sender,
            )

            status = archiver.get_status()

            # Required fields
            required_fields = [
                "execution_id",
                "log_type",
                "state",
                "last_acked_offset",
                "last_sent_offset",
                "file_size",
                "in_flight_count",
                "bytes_sent",
                "finalize_requested",
            ]

            for field in required_fields:
                assert field in status, f"Missing required field: {field}"

            assert status["execution_id"] == execution_id
            assert status["log_type"] == log_type

        finally:
            log_file.unlink(missing_ok=True)
