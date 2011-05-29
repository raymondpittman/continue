"""An implementation of efficient CTW model updating.

The code is inspired by the CTW implementation for MC-AIXI:
"A Monte-Carlo AIXI Approximation"
Joel Veness, Kee Siong Ng, Marcus Hutter, William Uther, David Silver
http://jveness.info/software/default.html
"""

import math

def create_model(deterministic=False, max_depth=None):
    if deterministic:
        estim_update = _determ_estim_update
    else:
        estim_update = _kt_estim_update

    return _CtModel(estim_update, max_depth)


NO_CHILDREN = [None, None]
LOG_ONE = 0.0
LOG_ONE_HALF = math.log(0.5)
LOG_ZERO = float("-inf")


class _CtModel:
    def __init__(self, estim_update, max_depth=None):
        """Creates a Context Tree model.
        """
        self.estim_update = estim_update
        self.max_depth = max_depth
        self.history = []
        self.root = _Node()

    def see_generated(self, bits):
        """Updates the model parameters
        after seeing next generated bits.
        The model should also generate them with high probability.
        """
        for bit in bits:
            self._see_generated_bit(bit)

    def switch_history(self):
        """Keeps the learned model, but starts
        with an empty history.
        It allows to switch between sequence examples.
        """
        self.history = []

    def see_added(self, bits):
        """Adds a historic bit without affecting the model parameters.
        The bit is outside of the model scope.
        Its P(bit) is not bound to this model.
        """
        # Note that it is enough to keep just the last
        # max_depth bits of history.
        self.history += bits

    def _see_generated_bit(self, bit):
        """Updates the counts and precomputed probabilities
        on the context path.
        """
        context = self._get_context()
        path = _get_context_path(self.root, context, save_nodes=True)

        # If the node children are not updated by the bit,
        # their model is later complemented with p_uncovered.
        # The "THE CONTEXT-TREE WEIGHTING METHOD: EXTENSIONS" paper
        # calls such nodes "has a tail".
        # Our p_uncovered updating is OK with history switching
        # and history adding by see_added().
        path[-1].log_p_uncovered += LOG_ONE_HALF

        for node in reversed(path):
            node.log_p_estim += self.estim_update(bit, node.counts)
            node.counts[bit] += 1

            # No weighting is used, if the node has no children.
            if node.children == NO_CHILDREN:
                node.log_pw = node.log_p_estim
            else:
                log_p0context = _child_log_pw(node, 0)
                log_p1context = _child_log_pw(node, 1)
                childrens_log_p = (log_p0context + log_p1context +
                        node.log_p_uncovered)
                node.log_pw = _avg_log_p(node.log_p_estim, childrens_log_p)

        self.history.append(bit)

    def predict_one(self):
        """Computes the conditional probability
        P(Next_bit=1|history).
        """
        # The implementation does the following
        # without making permanent changes by see_generated():
        #     p_given = self.root.pw
        #     see_generated(bits=[1])
        #     p_seq = self.root.pw
        #     return p_seq / float(p_given)
        bit = 1
        context = self._get_context()
        path = _get_context_path(self.root, context)
        assert len(path) == len(context) + 1

        new_log_pw = None
        for i, (child_bit, node) in enumerate(
                zip([None] + context, reversed(path))):
            log_p_estim = (node.log_p_estim +
                    self.estim_update(bit, node.counts))
            # The NO_CHILDREN test would not be enough
            # if save_nodes=False is used.
            if new_log_pw is None and node.children == NO_CHILDREN:
                new_log_pw = log_p_estim
            else:
                log_p_uncovered = node.log_p_uncovered
                if i == 0:
                    p_uncovered += LOG_ONE_HALF

                # The context can be shorter than
                # the existing tree depth, when switching history.
                # Both children will carry valid probability.
                if child_bit is None:
                    child_bit = 0
                    new_log_pw = _child_log_pw(node, child_bit)

                other_child_log_pw = _child_log_pw(node, 1 - child_bit)
                childrens_log_p = (new_log_pw + other_child_log_pw +
                        log_p_uncovered)
                new_log_pw = _avg_log_p(log_p_estim, childrens_log_p)

        return math.exp(new_log_pw - self.root.log_pw)

    def _get_context(self):
        """Returns the recent context.
        """
        context = self.history
        if self.max_depth is not None and len(context) > self.max_depth:
            context = context[len(context) - self.max_depth:]
            assert len(context) == self.max_depth
        return context

    def get_history_p(self):
        """Returns the probability of the whole history.
        """
        return math.exp(self.root.log_pw)


def _get_context_path(root, context, save_nodes=False):
    """Returns a path from the root to the start of the context.
    """
    path = [root]
    node = root
    for i, bit in enumerate(reversed(context)):
        child = node.children[bit]
        if child is None:
            child = _Node()
            if save_nodes:
                node.children[bit] = child

        path.append(child)
        node = child

    return path


def _avg_log_p(a_log_p, b_log_p):
    """Returns log(0.5 * (a_p + b_p)).
    It is equal to: log(0.5) + log(b_p * (1 +  a_p/b_p)).
    """
    log_rate = a_log_p - b_log_p
    # It is OK to use x instead of log(1 + e**x) if x is big.
    # This trick is from Joel Veness's CTW source code.
    if log_rate >= 100:
        log_one_plus = log_rate
    else:
        log_one_plus = math.log(1 + math.exp(log_rate))

    return LOG_ONE_HALF + b_log_p + log_one_plus


def _child_log_pw(node, child_bit):
    child = node.children[child_bit]
    if child is None:
        return LOG_ONE
    return child.log_pw


class _Node:
    def __init__(self):
        # It is needed to work with log(p)
        # when working with probabilities lower
        # than 1e-324.
        self.log_p_estim = LOG_ONE
        self.log_pw = LOG_ONE
        self.log_p_uncovered = LOG_ONE
        self.counts = [0, 0]
        self.children = [None, None]

    def __repr__(self):
        return "Node" + str(dict(
            log_p_estim=self.log_p_estim,
            log_pw=self.log_pw,
            log_p_uncovered=self.log_p_uncovered,
            counts=self.counts))


def _determ_estim_update(new_bit, counts):
    """Beliefs only a sequence of all ones or zeros.
    """
    new_counts = counts[:]
    new_counts[new_bit] += 1
    if new_counts[0] > 0 and new_counts[1] > 0:
        return LOG_ZERO

    log_p_new = _determ_log_p(new_counts)
    log_p_old = _determ_log_p(counts)
    return log_p_new - log_p_old


def _determ_log_p(counts):
    if counts[0] == 0 and counts[1] == 0:
        return LOG_ONE
    if counts[0] == 0:
        return LOG_ONE_HALF
    if counts[1] == 0:
        return LOG_ONE_HALF
    return LOG_ZERO


def _kt_estim_update(new_bit, counts):
    """Computes log(P(Next_bit=new_bit|counts))
    for the the Krichevski-Trofimov estimator.
    """
    return math.log((counts[new_bit] + 0.5) / float((sum(counts) + 1)))

