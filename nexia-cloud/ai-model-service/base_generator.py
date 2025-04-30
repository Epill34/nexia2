from typing import Dict, List, Optional, Any
import re

class BaseGenerator:
    """Base class for all response generators"""
    
    def __init__(self, generator_id: str, description: str):
        self.generator_id = generator_id
        self.description = description
        self.triggers = []  # List of trigger words/phrases
        self.applicable_states = []  # List of conversation flow states where this applies
        self.negative_triggers = []  # Words that should prevent matching
        self.usage_count = 0
        self.success_count = 0
        
    def matches(self, text: str, state: Optional[int] = None, context: Optional[Dict] = None) -> bool:
        """
        Check if this generator matches the given text and context.
        Returns True if there's a match, False otherwise.
        """
        # Skip if we're in a state where this generator doesn't apply
        if state is not None and self.applicable_states and state not in self.applicable_states:
            return False
            
        # Check for negative triggers first (words that should prevent matching)
        text_lower = text.lower()
        if any(neg_trigger.lower() in text_lower for neg_trigger in self.negative_triggers):
            return False
            
        # Check for positive triggers
        return any(trigger.lower() in text_lower for trigger in self.triggers)
        
    def generate(self, text: str, customer_data: Optional[Dict] = None, 
                conversation_history: Optional[List] = None, state: Optional[int] = None) -> str:
        """
        Generate a response for the given text and context.
        Must be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement generate()")
        
    def track_usage(self, success: bool = True):
        """Track usage metrics"""
        self.usage_count += 1
        if success:
            self.success_count += 1