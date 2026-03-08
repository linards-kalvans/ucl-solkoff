"""Tests for Elo rating calculator module."""
import math
import pytest
from unittest.mock import Mock, call
from backend.elo_calculator import (
    EloCalculator,
    DEFAULT_RATING,
    K_FACTOR,
    HOME_ADVANTAGE,
    MIN_MATCHES_FOR_CONFIDENCE,
)
from backend.database import Database


@pytest.fixture
def mock_db():
    return Mock(spec=Database)


@pytest.fixture
def calculator(mock_db):
    return EloCalculator(mock_db)


# --- Initialization ---

def test_initialization(calculator, mock_db):
    assert calculator.db is mock_db


# --- _expected_score ---

def test_expected_score_equal_ratings(calculator):
    result = calculator._expected_score(1500.0, 1500.0)
    assert abs(result - 0.5) < 1e-9


def test_expected_score_higher_a_wins(calculator):
    result = calculator._expected_score(1600.0, 1500.0)
    assert result > 0.5


def test_expected_score_lower_a_loses(calculator):
    result = calculator._expected_score(1400.0, 1500.0)
    assert result < 0.5


def test_expected_score_home_advantage_helps_a(calculator):
    without = calculator._expected_score(1500.0, 1500.0, 0.0)
    with_adv = calculator._expected_score(1500.0, 1500.0, HOME_ADVANTAGE)
    assert with_adv > without


def test_expected_score_sums_to_one(calculator):
    r_a, r_b = 1550.0, 1480.0
    exp_a = calculator._expected_score(r_a, r_b)
    exp_b = calculator._expected_score(r_b, r_a)
    assert abs(exp_a + exp_b - 1.0) < 1e-9


# --- _goal_multiplier ---

def test_goal_multiplier_draw(calculator):
    # goal_diff = 0 → 1 + ln(1) = 1.0
    assert calculator._goal_multiplier(0) == pytest.approx(1.0)


def test_goal_multiplier_one_goal(calculator):
    assert calculator._goal_multiplier(1) == pytest.approx(1.0 + math.log(2))


def test_goal_multiplier_large_margin(calculator):
    assert calculator._goal_multiplier(5) > calculator._goal_multiplier(1)


# --- _actual_score ---

def test_actual_score_home_win(calculator):
    assert calculator._actual_score(3, 0) == (1.0, 0.0)


def test_actual_score_draw(calculator):
    assert calculator._actual_score(1, 1) == (0.5, 0.5)


def test_actual_score_away_win(calculator):
    assert calculator._actual_score(0, 2) == (0.0, 1.0)


# --- calculate_all ---

def test_calculate_all_no_matches(calculator, mock_db):
    mock_db.fetchall.return_value = []
    calculator.calculate_all()
    mock_db.execute.assert_not_called()
    mock_db.commit.assert_called_once()


def test_calculate_all_single_match_calls_execute_for_both_teams(calculator, mock_db):
    # One finished match: team 1 (home) beats team 2 (away) 2-0
    mock_db.fetchall.return_value = [(1, 2, 2, 0)]
    calculator.calculate_all()
    assert mock_db.execute.call_count == 2
    mock_db.commit.assert_called_once()


def test_calculate_all_winner_rating_increases(calculator, mock_db):
    mock_db.fetchall.return_value = [(1, 2, 3, 0)]
    calculator.calculate_all()

    calls = mock_db.execute.call_args_list
    # Find the call for team 1 (home winner)
    team1_call = next(c for c in calls if c[0][1][0] == 1)
    team1_rating = team1_call[0][1][1]
    assert team1_rating > DEFAULT_RATING


def test_calculate_all_loser_rating_decreases(calculator, mock_db):
    mock_db.fetchall.return_value = [(1, 2, 3, 0)]
    calculator.calculate_all()

    calls = mock_db.execute.call_args_list
    team2_call = next(c for c in calls if c[0][1][0] == 2)
    team2_rating = team2_call[0][1][1]
    assert team2_rating < DEFAULT_RATING


def test_calculate_all_draw_ratings_sum_to_double_default(calculator, mock_db):
    # Elo is zero-sum: the two ratings always sum to 2 * DEFAULT_RATING
    mock_db.fetchall.return_value = [(1, 2, 1, 1)]
    calculator.calculate_all()

    calls = mock_db.execute.call_args_list
    team1_call = next(c for c in calls if c[0][1][0] == 1)
    team2_call = next(c for c in calls if c[0][1][0] == 2)
    total = team1_call[0][1][1] + team2_call[0][1][1]
    assert abs(total - 2 * DEFAULT_RATING) < 1e-3


def test_calculate_all_stores_matches_played(calculator, mock_db):
    mock_db.fetchall.return_value = [(1, 2, 1, 0), (1, 3, 2, 1)]
    calculator.calculate_all()

    calls = mock_db.execute.call_args_list
    team1_call = next(c for c in calls if c[0][1][0] == 1)
    matches_played = team1_call[0][1][2]
    assert matches_played == 2


def test_calculate_all_upsert_sql(calculator, mock_db):
    mock_db.fetchall.return_value = [(1, 2, 1, 0)]
    calculator.calculate_all()

    for c in mock_db.execute.call_args_list:
        sql = c[0][0]
        assert "INSERT INTO elo_ratings" in sql
        assert "ON CONFLICT" in sql


# --- get_rating ---

def test_get_rating_found(calculator, mock_db):
    mock_db.fetchone.return_value = (1523.5, 8)
    result = calculator.get_rating(1)
    assert result == (1523.5, 8)


def test_get_rating_not_found(calculator, mock_db):
    mock_db.fetchone.return_value = None
    assert calculator.get_rating(99) is None


# --- get_win_probability ---

def test_get_win_probability_equal_ratings(calculator, mock_db):
    mock_db.fetchone.side_effect = [(1500.0, 10), (1500.0, 10)]
    result = calculator.get_win_probability(1, 2)
    assert result is not None
    assert result["method"] == "elo"
    assert abs(result["team1Win"] - 0.5) < 0.001
    assert abs(result["team2Win"] - 0.5) < 0.001
    assert result["draw"] == 0.0
    assert abs(result["team1Win"] + result["team2Win"] - 1.0) < 1e-6


def test_get_win_probability_higher_rated_favored(calculator, mock_db):
    mock_db.fetchone.side_effect = [(1600.0, 10), (1500.0, 10)]
    result = calculator.get_win_probability(1, 2)
    assert result["team1Win"] > 0.5
    assert result["team2Win"] < 0.5


def test_get_win_probability_returns_none_if_team1_missing(calculator, mock_db):
    mock_db.fetchone.side_effect = [None, (1500.0, 10)]
    assert calculator.get_win_probability(1, 2) is None


def test_get_win_probability_returns_none_if_team2_missing(calculator, mock_db):
    mock_db.fetchone.side_effect = [(1500.0, 10), None]
    assert calculator.get_win_probability(1, 2) is None


def test_get_win_probability_returns_none_if_insufficient_matches(calculator, mock_db):
    mock_db.fetchone.side_effect = [
        (1500.0, MIN_MATCHES_FOR_CONFIDENCE - 1),
        (1500.0, 10),
    ]
    assert calculator.get_win_probability(1, 2) is None


def test_get_win_probability_includes_elo_details(calculator, mock_db):
    mock_db.fetchone.side_effect = [(1550.0, 12), (1480.0, 9)]
    result = calculator.get_win_probability(1, 2)
    assert "team1EloRating" in result
    assert "team2EloRating" in result
    assert "team1MatchesPlayed" in result
    assert "team2MatchesPlayed" in result
    assert result["team1EloRating"] == 1550.0
    assert result["team2EloRating"] == 1480.0
