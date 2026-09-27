"""NexScore constants (spec Section 20). Tune here, nowhere else."""
UPVOTE_RECEIVED = 2
DOWNVOTE_RECEIVED = -1
ANSWER_ACCEPTED = 15
RESOURCE_RATED_HIGH = 5
RESOURCE_DOWNLOADED = 1
CONTENT_REMOVED = -20

DAILY_CAP = 100              # max positive points a user can earn per day
PER_ACTOR_DAILY_CAP = 10     # max positive points from one voter to one author per day
VOTE_REASONS = {"upvote_received", "downvote_received"}
