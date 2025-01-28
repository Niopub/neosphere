
import unittest
from ..extra_tools.message_logger import MessageLogger, get_claude_like_message, get_chatgpt_like_message

class TestMessageLogger(unittest.TestCase):

    def setUp(self):
        self.logger = MessageLogger(get_claude_like_message)

    def test_log_response(self):
        self.logger.clear_history("test_group")
        self.logger.log_response("test_group", role="user", content="Test message")
        self.assertEqual(len(self.logger.history["test_group"]), 1)
        self.assertEqual(self.logger.history["test_group"][0][0]["content"], "Test message")

    def test_log_response_with_replace(self):
        self.logger.clear_history("test_group")
        self.logger.log_response("test_group", role="user", content="Test message")
        self.logger.log_response("test_group", role="user", content="Replaced message", index=0, replace=True)
        self.assertEqual(len(self.logger.history["test_group"]), 1)
        self.assertEqual(self.logger.history["test_group"][0][0]["content"], "Replaced message")

    def test_log_response_with_insert(self):
        self.logger.clear_history("test_group")
        self.logger.log_response("test_group", role="user", content="First message")
        self.logger.log_response("test_group", role="user", content="Inserted message", index=0)
        self.assertEqual(len(self.logger.history["test_group"]), 2)
        self.assertEqual(self.logger.history["test_group"][0][0]["content"], "Inserted message")

    def test_get_last_n_messages(self):
        self.logger.clear_history("test_group")
        for i in range(5):
            self.logger.log_response("test_group", role="user", content=f"Message {i}")
        last_messages = self.logger.get_last_n_messages(3, "test_group")
        self.assertEqual(len(last_messages), 3)
        self.assertEqual(last_messages[0]["content"], "Message 2")
        self.assertEqual(last_messages[1]["content"], "Message 3")
        self.assertEqual(last_messages[2]["content"], "Message 4")

    def test_get_last_n_messages_with_metadata(self):
        self.logger.clear_history("test_group")
        for i in range(5):
            if i == 2:
                self.logger.log_response("test_group", role="user", content=f"Message {i}", from_id="a.bc_bot", tool_use_id="qwe123123123", media_ids=["media123", "media234"])
            else:
                self.logger.log_response("test_group", role="user", content=f"Message {i}", from_id="a.bc_bot", media_ids=["media123", "media234"])
        last_messages = self.logger.get_last_n_messages(3, "test_group", include_metadata=True)
        self.assertEqual(len(last_messages), 3)
        self.assertTrue(isinstance(last_messages[0][0]["content"], list))
        self.assertTrue(isinstance(last_messages[0][0]["content"][0], dict))
        self.assertEqual(last_messages[0][0]["content"][0]['content'], "Message 2")
        self.assertEqual(last_messages[0][1]["from_id"], "a.bc_bot")
        self.assertEqual(last_messages[1][0]["content"], "Message 3")
        self.assertEqual(last_messages[1][1]["from_id"], "a.bc_bot")
        self.assertEqual(last_messages[2][0]["content"], "Message 4")
        self.assertEqual(last_messages[2][1]["from_id"], "a.bc_bot")

if __name__ == "__main__":
    unittest.main()