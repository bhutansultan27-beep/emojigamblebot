import random
from typing import List, Dict, Optional, Any

# --- 1. Card and Deck Definitions ---

# Ranks mapping 'T' for Ten, 'J/Q/K' for 10, 'A' for 11/1
RANKS = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9,
    'T': 10, 'J': 10, 'Q': 10, 'K': 10, 'A': 11
}
SUITS = ['H', 'D', 'C', 'S'] # Hearts, Diamonds, Clubs, Spades
CARD_FACES = {'H': '♥', 'D': '♦', 'C': '♣', 'S': '♠'}

class Card:
    """Represents a single playing card."""
    def __init__(self, rank: str, suit: str):
        self.rank = rank
        self.suit = suit
        self.value = RANKS[rank]

    def __str__(self):
        """Returns a human-readable and attractive card representation."""
        return f"[{self.rank}{CARD_FACES.get(self.suit, '')}]"

class Deck:
    """Represents the shoe of cards, typically 6-8 decks, shuffled."""
    def __init__(self, num_decks: int = 6):
        self.cards: List[Card] = []
        self._initialize_cards(num_decks)
        self.shuffle()

    def _initialize_cards(self, num_decks: int):
        """Creates the specified number of decks."""
        for _ in range(num_decks):
            for rank in RANKS:
                for suit in SUITS:
                    self.cards.append(Card(rank, suit))

    def shuffle(self):
        """Shuffles the deck."""
        random.shuffle(self.cards)

    def deal_card(self) -> Card:
        """Deals one card from the deck. Reshuffles if empty."""
        if not self.cards or len(self.cards) < 52: # Reshuffle if less than one full deck remains
            self.cards = []  # Clear the deck first
            self._initialize_cards(6)
            self.shuffle()
        return self.cards.pop()

# --- 2. Hand Definitions ---

class Hand:
    """Represents a player or dealer's hand, handling soft/hard value calculation."""
    def __init__(self, cards: Optional[List[Card]] = None, is_split: bool = False):
        self.cards: List[Card] = cards if cards is not None else []
        self.is_split = is_split
        self.value, self.soft = self._calculate_value()

    def add_card(self, card: Card):
        """Adds a card and recalculates the hand value."""
        self.cards.append(card)
        self.value, self.soft = self._calculate_value()

    def _calculate_value(self) -> tuple[int, bool]:
        """Calculates the best possible score (value) and determines if it is soft."""
        value = sum(card.value for card in self.cards)
        num_aces = sum(1 for card in self.cards if card.rank == 'A')
        is_soft = False

        # Adjust for soft aces (Ace = 11 down to 1)
        while value > 21 and num_aces > 0:
            value -= 10  # Change an Ace from 11 to 1
            num_aces -= 1

        # Check for soft status: True if an Ace is present and counted as 11
        # This requires checking the value *before* the final adjustment
        initial_value_check = sum(card.value for card in self.cards)
        if num_aces > 0 and value <= 21 and initial_value_check > value:
            is_soft = True
        
        return value, is_soft

    def is_blackjack(self) -> bool:
        """Checks for natural 21 (only possible with 2 cards, and not a split hand)."""
        return len(self.cards) == 2 and self.value == 21 and not self.is_split

    def is_busted(self) -> bool:
        """Checks if the hand value exceeds 21."""
        return self.value > 21

    def get_cards_str(self, hidden: bool = False) -> str:
        """Returns the card string, optionally hiding the hole card (for dealer)."""
        if not hidden or len(self.cards) < 2:
            return ' '.join(str(card) for card in self.cards)
        else:
            # Hide the second card
            return f"{str(self.cards[0])} [??]"

# --- 3. Game Logic ---

class BlackjackGame:
    """Manages the state and rules for a single game of Blackjack."""
    
    # Standard Casino Rule: Dealer hits on soft 17 (H17)
    DEALER_STANDS_ON_SOFT_17 = False

    def __init__(self, bet_amount: int, deck: Optional[Deck] = None):
        self.deck = deck if deck else Deck()
        # Player Hands: List of dictionaries to support splitting
        self.player_hands: List[Dict[str, Any]] = [
            {'hand': Hand(), 'bet': bet_amount, 'status': 'Playing', 'actions': {'can_split': False, 'can_double': False, 'can_surrender': False}}
        ]
        self.dealer_hand = Hand()
        self.current_hand_index = 0
        self.is_insurance_available = False
        self.insurance_bet = 0
        self.insurance_payout = 0
        self.initial_bet = bet_amount

    # --- Setup and Utility ---

    def start_game(self) -> str:
        """Deals initial cards and checks for immediate Blackjacks/Insurance."""
        # Deal two cards to player and two to dealer
        for _ in range(2):
            self.player_hands[0]['hand'].add_card(self.deck.deal_card())
            self.dealer_hand.add_card(self.deck.deal_card())

        # Check for Insurance opportunity (Dealer upcard is an Ace)
        if self.dealer_hand.cards[0].rank == 'A':
            self.is_insurance_available = True
            
        # Check for immediate Blackjack
        if self.player_hands[0]['hand'].is_blackjack():
            self.player_hands[0]['status'] = 'Blackjack'
            # Force current_hand_index to end game
            self.current_hand_index = 1 
            # If dealer upcard is Ace, check hole card before resolving
            if self.dealer_hand.cards[0].rank == 'A' or self.dealer_hand.cards[0].value == 10:
                # Dealer could have blackjack too
                if self.dealer_hand.is_blackjack():
                    self._resolve_game()
                    return "Push - Both have Blackjack! Game over."
                else:
                    self._resolve_game()
                    return "Blackjack! Game over."
            else:
                self._resolve_game()
                return "Blackjack! Game over."

        self._check_available_actions()
        return "Game started. Your turn."

    def _check_available_actions(self):
        """Sets the possible actions for the current player hand."""
        if self.current_hand_index >= len(self.player_hands):
            return # No hands left to check

        current_state = self.player_hands[self.current_hand_index]
        hand = current_state['hand']
        
        # Reset actions
        current_state['actions'] = {'can_split': False, 'can_double': False, 'can_surrender': False}

        # Actions are only available if the hand is 'Playing' and not busted
        if current_state['status'] != 'Playing':
            return
            
        # Surrender, Double, and Split are only available on the initial two cards
        if len(hand.cards) == 2:
            # ONLY double on hard 9, 10, or 11
            # Hard 9, 10, 11 means no Ace being used as 11 to reach that value
            if not hand.soft and hand.value in [9, 10, 11]:
                current_state['actions']['can_double'] = True
            
            # Surrender only allowed on the *very first* hand
            if self.current_hand_index == 0:
                current_state['actions']['can_surrender'] = True
            
            # Split only allowed if ranks are equal
            if hand.cards[0].rank == hand.cards[1].rank:
                current_state['actions']['can_split'] = True

    def _advance_hand(self):
        """Moves to the next hand or triggers the dealer turn if all player hands are done."""
        self.current_hand_index += 1
        
        if self.current_hand_index >= len(self.player_hands):
            # All player hands resolved (Stood, Busted, Doubled, Surrendered, or Blackjack)
            self._dealer_turn()
        else:
            # Move to the next hand
            self._check_available_actions()
            current_hand = self.player_hands[self.current_hand_index]
            # Immediately check if the hand is already resolved (e.g., from Ace split)
            if current_hand['status'] != 'Playing':
                self._advance_hand()

    # --- Player Actions ---

    def hit(self) -> str:
        """Player requests another card."""
        if not self._is_current_hand_actionable():
            return "Error: Cannot hit. Hand is not in a 'Playing' state."

        current_hand_state = self.player_hands[self.current_hand_index]
        hand = current_hand_state['hand']
        hand.add_card(self.deck.deal_card())
        
        # Disable all special actions after the first Hit
        current_hand_state['actions'] = {} 
        
        message = f"Hit. New value: {hand.value}."

        if hand.is_busted():
            current_hand_state['status'] = 'Bust'
            self._advance_hand()
            message = f"Bust! Value: {hand.value}."
        elif hand.value == 21:
            current_hand_state['status'] = 'Stood'
            self._advance_hand()
            message = "Reached 21 and stood."
        
        return message

    def stand(self) -> str:
        """Player chooses to keep their current hand."""
        if not self._is_current_hand_actionable():
            return "Error: Cannot stand. Hand is not in a 'Playing' state."
            
        self.player_hands[self.current_hand_index]['status'] = 'Stood'
        self._advance_hand()
        return "Stood."

    def double_down(self) -> str:
        """Player doubles their bet and takes exactly one more card."""
        current_hand_state = self.player_hands[self.current_hand_index]
        if not current_hand_state['actions'].get('can_double'):
            return "Error: Cannot Double Down. Only allowed on the initial two cards."
        
        # Increase bet
        current_hand_state['bet'] *= 2
        
        # Hit exactly once
        current_hand_state['hand'].add_card(self.deck.deal_card())
        
        # Set status and advance
        current_hand_state['status'] = 'Doubled'
        if current_hand_state['hand'].is_busted():
            current_hand_state['status'] = 'Bust'
        
        self._advance_hand()
        return f"Doubled down. New bet: {current_hand_state['bet']}. Final card drawn."

    def split(self) -> str:
        """Player splits a pair into two new hands."""
        current_hand_state = self.player_hands[self.current_hand_index]
        if not current_hand_state['actions'].get('can_split'):
            return "Error: Cannot Split. Must have two cards of the same rank and must be the first action."

        hand = current_hand_state['hand']
        card1 = hand.cards[0]
        card2 = hand.cards[1]
        bet = current_hand_state['bet']
        
        # 1. Update the current hand (Hand 1)
        current_hand_state['hand'] = Hand(cards=[card1], is_split=True)
        current_hand_state['hand'].add_card(self.deck.deal_card())
        current_hand_state['actions'] = {} # Reset actions for Hand 1

        # 2. Create the new hand (Hand 2)
        new_hand_state: Dict[str, Any] = {
            'hand': Hand(cards=[card2], is_split=True), 
            'bet': bet, 
            'status': 'Playing',
            # Re-splitting Aces is often disallowed, so we keep actions disabled for now.
            'actions': {'can_split': False, 'can_double': True, 'can_surrender': False} 
        }
        new_hand_state['hand'].add_card(self.deck.deal_card())
        
        # Insert the new hand immediately after the current one
        self.player_hands.insert(self.current_hand_index + 1, new_hand_state)
        
        message = f"Split successful. You now have {len(self.player_hands)} hands."
        
        # Special rule for splitting Aces: only one card is drawn per Ace, then the hand must stand.
        if card1.rank == 'A':
            current_hand_state['status'] = 'Stood'
            new_hand_state['status'] = 'Stood'
            self._advance_hand() # Move past both hands automatically
            message += " (Aces split, drawing one card each, then standing automatically.)"
        else:
            # Recalculate actions for the current hand (Hand 1)
            self._check_available_actions() 
        
        return message

    def surrender(self) -> str:
        """Player forfeits the hand and loses half the bet."""
        current_hand_state = self.player_hands[self.current_hand_index]
        if not current_hand_state['actions'].get('can_surrender'):
            return "Error: Cannot Surrender. Only allowed on the initial hand as the first action."

        # Player gets half the bet back (payout is -0.5 * bet)
        current_hand_state['payout'] = -current_hand_state['bet'] / 2
        current_hand_state['status'] = 'Surrendered'
        current_hand_state['actions'] = {} # Clear actions
        
        # All hands must resolve after a surrender, so we advance to dealer turn
        self.current_hand_index = len(self.player_hands) - 1 
        self._advance_hand()
        return f"Surrendered. You lose half your bet: {current_hand_state['bet'] / 2}"

    def take_insurance(self) -> str:
        """Player places a side bet that the dealer has Blackjack."""
        if not self.is_insurance_available:
            return "Error: Insurance is not available."
        
        self.insurance_bet = self.initial_bet / 2 
        self.is_insurance_available = False # Insurance decision is made once
        return f"Insurance taken for {self.insurance_bet}."

    def _is_current_hand_actionable(self) -> bool:
        """Helper to check if the current hand can perform an action."""
        return (self.current_hand_index < len(self.player_hands) and 
                self.player_hands[self.current_hand_index]['status'] == 'Playing')

    # --- Dealer Logic and Resolution ---

    def _dealer_turn(self):
        """Executes the dealer's drawing phase and resolves all bets."""
        
        # 1. Resolve Insurance (paid 2:1 if dealer has Blackjack, otherwise lost)
        self._resolve_insurance()
        
        # 2. Dealer hits based on casino rules
        dealer_hand = self.dealer_hand
        
        # Dealer must hit until hard 17 or higher. 
        # Standard rule: Dealer stands on Soft 17 (Ace and 6, total 17)
        while True:
            value = dealer_hand.value
            soft = dealer_hand.soft

            if value < 17:
                dealer_hand.add_card(self.deck.deal_card())
            elif value == 17 and soft and not self.DEALER_STANDS_ON_SOFT_17:
                # Rule: Dealer must hit on Soft 17 (rare but possible rule)
                dealer_hand.add_card(self.deck.deal_card())
            else: 
                # value >= 17 (or 17 soft and standing)
                break
                
            if dealer_hand.is_busted():
                break

        # 3. Resolve all player hands
        self._resolve_game()

    def _resolve_insurance(self):
        """Resolves the insurance bet."""
        if self.insurance_bet > 0:
            if self.dealer_hand.is_blackjack():
                # Insurance wins 2:1
                self.insurance_payout = self.insurance_bet * 2
            else:
                # Insurance lost
                self.insurance_payout = -self.insurance_bet

    def _resolve_game(self):
        """Calculates the payout for every player hand based on final dealer score."""
        dealer_value = self.dealer_hand.value
        dealer_busted = self.dealer_hand.is_busted()
        
        for hand_state in self.player_hands:
            if 'payout' in hand_state:
                # Hand already resolved (e.g., Surrender)
                continue

            hand = hand_state['hand']
            bet = hand_state['bet']
            status = hand_state['status']
            
            payout = 0
            
            if status == 'Blackjack':
                # Player Blackjack always pays 3:2 unless dealer also has Blackjack (Push)
                if self.dealer_hand.is_blackjack():
                    payout = 0 
                else:
                    payout = bet * 1.5 
            
            elif status == 'Bust':
                payout = -bet # Player loses bet
            
            elif dealer_busted:
                payout = bet # Dealer bust, player wins 1:1
                
            else:
                player_value = hand.value
                if player_value > dealer_value:
                    payout = bet # Win 1:1
                elif player_value < dealer_value:
                    payout = -bet # Loss
                else:
                    payout = 0 # Push (Tie)

            hand_state['payout'] = payout

    # --- Bot Integration Output ---

    def get_game_state(self) -> Dict[str, Any]:
        """Returns a dict containing all necessary data for the bot to display and generate buttons."""
        
        game_over = self.current_hand_index >= len(self.player_hands)
        
        player_hands_data = []
        for i, h_state in enumerate(self.player_hands):
            is_current = (i == self.current_hand_index and not game_over and h_state['status'] == 'Playing')
            
            hand_data = {
                'id': i,
                'cards': h_state['hand'].get_cards_str(),
                'value': h_state['hand'].value,
                'bet': h_state['bet'],
                'status': h_state['status'],
                'is_current_turn': is_current,
                'actions': h_state.get('actions', {}), # Available actions (hit, stand, split, double, surrender)
                'payout': h_state.get('payout')
            }
            player_hands_data.append(hand_data)
        
        # Determine dealer display
        dealer_cards_str = self.dealer_hand.get_cards_str(hidden=not game_over)
        dealer_value_display = str(self.dealer_hand.cards[0].value) if not game_over else str(self.dealer_hand.value)
        
        total_payout = sum(h_state.get('payout', 0) for h_state in self.player_hands) + self.insurance_payout

        return {
            'game_over': game_over,
            'dealer': {
                'cards': dealer_cards_str,
                'value': dealer_value_display,
                'is_blackjack': self.dealer_hand.is_blackjack(),
                'final_status': 'Bust' if self.dealer_hand.is_busted() else 'Stood'
            },
            'player_hands': player_hands_data,
            'is_insurance_available': self.is_insurance_available and not game_over,
            'insurance_bet': self.insurance_bet,
            'insurance_payout': self.insurance_payout,
            'total_payout': total_payout if game_over else None,
            'current_hand_index': self.current_hand_index,
        }

# --- 4. Bot Integration Example ---
# This section demonstrates how a Telegram bot would use the class.

def handle_bj_command(user_id: str, bet_amount: int, game_sessions: Dict[str, BlackjackGame]) -> Dict[str, Any]:
    """
    Initializes a new game session.
    In a real bot, game_sessions would be a persistent dictionary (e.g., Redis/database).
    """
    if user_id in game_sessions:
        return {"error": "A game is already in progress. Finish it or use /stand."}
        
    game = BlackjackGame(bet_amount)
    game.start_game()
    game_sessions[user_id] = game
    
    return game.get_game_state()

def handle_player_action(user_id: str, action: str, game_sessions: Dict[str, BlackjackGame]) -> Dict[str, Any]:
    """Handles player input (hit, stand, double, split, surrender, insurance)."""
    if user_id not in game_sessions:
        return {"error": "No game in progress. Use /bj to start a new game."}

    game = game_sessions[user_id]
    
    # Execute the requested action
    action_method = getattr(game, action, None)
    
    if action_method and callable(action_method):
        action_result_message = action_method()
    else:
        return {"error": f"Invalid action: {action}."}

    # Get the updated state for display
    state = game.get_game_state()
    state['action_message'] = action_result_message
    
    if state['game_over']:
        # Cleanup the session
        del game_sessions[user_id]
        
    return state
    
# Example Usage Simulation (For testing the logic):
if __name__ == '__main__':
    print("--- Blackjack Game Logic Simulation ---")
    
    # 1. Start Game
    game_sessions = {}
    USER_ID = "test_user_123"
    BET = 100
    
    initial_state = handle_bj_command(USER_ID, BET, game_sessions)
    game = game_sessions[USER_ID]

    print(f"\n--- Initial Deal (Bet: {BET}) ---")
    print(f"Dealer's Card: {initial_state['dealer']['cards']} (Value: {initial_state['dealer']['value']})")
    for h in initial_state['player_hands']:
        print(f"Your Hand: {h['cards']} (Value: {h['value']}) | Status: {h['status']}")
        print(f"Available Actions: {h['actions']}")

    # 2. Example Action (Hit)
    if not initial_state['game_over'] and initial_state['player_hands'][0]['status'] == 'Playing':
        print("\n--- Player Hits ---")
        state_after_hit = handle_player_action(USER_ID, 'hit', game_sessions)
        print(f"Action Message: {state_after_hit['action_message']}")
        for h in state_after_hit['player_hands']:
            if h['is_current_turn']:
                print(f"Your New Hand: {h['cards']} (Value: {h['value']})")
        
        # 3. Example Action (Stand)
        if not state_after_hit['game_over'] and state_after_hit['player_hands'][state_after_hit['current_hand_index']]['status'] == 'Playing':
            print("\n--- Player Stands ---")
            final_state = handle_player_action(USER_ID, 'stand', game_sessions)
            
            print(f"Action Message: {final_state['action_message']}")
            print(f"\n--- Final Dealer/Payout ---")
            print(f"Dealer Final Hand: {final_state['dealer']['cards']} (Value: {final_state['dealer']['value']})")
            
            for h in final_state['player_hands']:
                print(f"Hand {h['id']}: {h['cards']} | Status: {h['status']} | Payout: {h['payout']}")
            
            print(f"Total Payout (Win/Loss): {final_state['total_payout']}")
