"""The "just for fun" answering-style profile (`live_genesis/profiles.py`) and its place on "How it works".
Offline: a profile is a pure count over answer texts; no model, no network, no stored state."""
import copy
import json
import re

import pytest

from live_genesis import archive, archive_pages, profiles
from test_archive import REPO, STATIC, assert_page_is_safe, ready  # noqa: F401
from test_archive_chatbots import visible

# A frozen copy of what was published on 19 Nov 2026 (four models, ten questions). The exact figures below are pinned
# to it, NOT to the live `published/` folder, which the owner adds to every month: a test must not break because
# real answers were published.
PUBLISHED = json.loads((REPO / 'tests' / 'fixtures' / 'profiles_published_2026-11-19.json').read_text(encoding='utf-8'))
LIVE = REPO / 'published' / 'archive.json'

# Hand-made answers, five or more per step, wild end first.
GREMLIN = ['OMG yes!! 😂', 'LOL nah, never! 🔥', 'Haha, totally! 😜', 'WOW, like, who even asks that? 😂', 'Nope!! 🤪', 'Yay, soup! 🎉']
CLOWN = ['Nah, it is a taco! 😜', 'Only on weekends.', 'Kinda, if you squint!', 'Ice cream wins! 🍦', 'Who wants to know?', 'A duck, obviously.']
FRIEND = ['It is a sandwich with a hat. 😄', 'Mondays are fine.', 'Over, every time! 🧻', 'Soup needs a spoon and a plan.', 'Pineapple stays.',
          'A horse-sized duck, please. 🦆']
SENSIBLE = ['It is a sandwich.', 'Mondays are fine.', 'Over is the usual way.', 'Cereal is not a soup.', 'Pineapple is fine on pizza.',
            'One large duck.']
GROWN_UP = ['Technically it is a sandwich, since the filling sits between two halves of one bread roll that is split along its side.',
            'It depends on the person, but most people rate Mondays lower than the other six days of the week for a simple reason.',
            'Arguably over, because the loose end is then easier to reach and the paper touches the wall a good deal less often.',
            'In general no: a soup is cooked and served as a dish of its own, while cereal is only soaked in cold milk.',
            'Probably the one large duck, since a single opponent is easier to keep in view than a hundred small ones at once.']


def test_each_of_the_five_steps_from_hand_made_answers():
    for step, texts in enumerate((GREMLIN, CLOWN, FRIEND, SENSIBLE, GROWN_UP), 1):
        found = profiles.profile(texts)
        assert (found['step'], found['name'], found['count']) == (step, profiles.STEPS[step - 1], len(texts)), (step, found)
        assert 1 <= len(found['because']) <= 2 and all(f'of {len(texts)} answers' in fact for fact in found['because'])
    assert profiles.STEPS == ('Chaos gremlin', 'Class clown', 'Good-time friend', 'The sensible one', 'The grown-up')
    assert profiles.profile(GREMLIN)['because'] == ['an emoji in 6 of 6 answers', 'slang like “OMG” in 6 of 6 answers']
    assert profiles.profile(GROWN_UP)['because'] == ['5 of 5 answers are plain statements, with no emoji, exclamation mark or slang',
                                                     'careful words like “technically” in 5 of 5 answers']
    # The middle step names one habit of each side.
    assert profiles.profile(FRIEND)['because'] == ['an emoji in 3 of 6 answers',
                                                   '3 of 6 answers are plain statements, with no emoji, exclamation mark or slang']


def test_signals_are_counted_once_per_answer_and_the_lists_are_short_and_english():
    found = profiles.signals('OMG!!! LOL!!! like, WHY? 😂😂😂')
    assert [found[name] for name in ('emoji', 'shout', 'slang', 'caps', 'ask', 'plain', 'care', 'long')] == [True] * 5 + [False] * 3
    assert found['slang_words'] == ['omg', 'lol', 'like,'] and profiles.points(found) == 10         # the most one answer can hold
    assert profiles.profile(['OMG!!! LOL!!! 😂😂😂'] * 5)['counts']['emoji'] == 5
    plain = profiles.signals('A hot dog is a hot dog.')
    assert plain['plain'] and profiles.points(plain) == -2
    # Whole words only; "like" needs its comma; an everyday abbreviation is not a shout; "AI" is too short to count.
    for text in ('I like soup and nahuatl and Thomas.', 'NASA and the USA use AI.', 'Lollipops, yeast, mehndi.'):
        assert profiles.signals(text)['plain'], text
    assert profiles.signals('I think it is, probably.')['care_words'] == ['probably', 'i think']
    assert profiles.signals(' '.join(['word'] * 20))['long'] is False and profiles.signals(' '.join(['word'] * 21))['long'] is True
    assert len(profiles.SLANG) <= 30 and len(profiles.CARE) <= 20 and 'ENGLISH ONLY' in profiles.__doc__
    assert all(word == word.lower() and word.isascii() for word in profiles.SLANG + profiles.CARE)
    assert dict(profiles.WILD) == {'emoji': 2, 'shout': 2, 'slang': 3, 'caps': 2, 'ask': 1}
    assert dict(profiles.CALM) == {'plain': -2, 'care': -3, 'long': -1} and profiles.BOUNDS == (4, 1.5, -1, -3)


def test_too_early_below_five_answers_and_a_value_on_a_bound_takes_the_wilder_step():
    for n in range(profiles.MIN_ANSWERS):
        early = profiles.profile(GREMLIN[:n])
        assert (early['step'], early['name'], early['because'], early['pick'], early['count']) == (None, 'Too early to tell', [], None, n)
    assert profiles.profile(GREMLIN[:5])['step'] == 1
    # Exactly on a bound: four points an answer is still step 1, a hair under is step 2; minus one is still the middle.
    assert profiles.profile(['Yes! 😂'] * 5)['total'] == 20 and profiles.profile(['Yes! 😂'] * 5)['step'] == 1
    assert profiles.profile(['Yes! 😂'] * 5 + ['So?'])['step'] == 2            # 21 points over six answers: 3.5
    on_bound = ['Fine.'] * 4 + ['So?'] * 2                                    # -8 and +2 over six answers: exactly -1
    assert profiles.profile(on_bound)['total'] == -6 and profiles.profile(on_bound)['step'] == 3
    assert profiles.profile(['Fine.'] * 5 + ['So?'])['step'] == 4             # -9 over six: -1.5


def test_the_same_answers_always_give_the_same_profile_and_the_oldest_wins_a_tie():
    first = profiles.profile(list(CLOWN))
    assert all(profiles.profile(list(CLOWN)) == first for _ in range(5))
    assert json.dumps(first, sort_keys=True) == json.dumps(profiles.profile(list(CLOWN)), sort_keys=True)
    # Two answers with the same points: the earlier one is the one shown.
    twins = profiles.profile(['Yes! 😂', 'No! 😂', 'Yes! 😂', 'No! 😂', 'Yes! 😂'])
    assert twins['pick'] == 0
    # Equally common signals are named in the listed order.
    assert profiles.profile(['Yes! 😂'] * 5)['because'] == ['an emoji in 5 of 5 answers', 'an exclamation mark in 5 of 5 answers']


def test_the_answer_shown_as_most_in_character_is_whole_and_short_or_there_is_none():
    long_one = 'OMG!!! ' + 'so ' * 60 + 'LOL 😂'
    assert len(long_one) > profiles.QUOTE_CHARS
    found = profiles.profile([long_one, 'Yes! 😂', 'Fine.', 'Fine.', 'Yes! 😂'])
    assert found['pick'] == 1                                             # the wildest one does not fit uncut, so the next
    assert profiles.profile([long_one] * 5)['pick'] is None               # and none fits: no quote at all
    # An answer holding a word we never put under a name is not picked either.
    assert profiles.profile(['I am the smartest! 😂 OMG'] + ['Yes! 😂'] * 4)['pick'] == 1


def test_no_forbidden_word_in_any_profile_text_the_code_can_produce():
    texts = profiles.every_text()
    assert len(texts) > 300 and set(profiles.STEPS) <= set(texts) and profiles.LABEL in texts and profiles.TOO_EARLY in texts
    banned = ('psycho', 'crazy', 'insane', 'unhinged', 'smart', 'best', 'conscious', 'feel')
    assert set(banned) <= set(profiles.FORBIDDEN)
    extra = ('mad', 'lunatic', 'mental', 'sane', 'stupid', 'dumb', 'genius', 'intelligen', 'winner', 'loser', 'worst', 'safe', 'round')
    for text in texts:
        for word in banned + extra:
            assert not re.search(r'\b' + word if word in extra else word, text.lower()), (word, text)
    # The page's own wording around a profile.
    how = archive_pages.render_chatbots(PUBLISHED)
    fun = ' '.join(re.findall(r'<div class="app-fun">(.*?)</div>', how))
    own = re.sub(r'<span class="fun-words">.*?</span>', '', fun)          # everything but the answerers' own recorded words
    assert own and not any(word in visible(own).lower() for word in banned)


def real_styles(payload=PUBLISHED):
    view = archive_pages._View(payload)
    return {key: style for key, (style, _) in archive_pages._styles(view).items()}


def test_the_four_published_models_get_the_profile_a_person_would_give_them():
    styles = real_styles()
    assert {key: (style['step'], style['name'], style['count']) for key, style in styles.items()} == {
        'phi4-mini': (2, 'Class clown', 10), 'gemma3-4b': (1, 'Chaos gremlin', 10),
        'granite42-3b': (3, 'Good-time friend', 10), 'qwen35-4b': (3, 'Good-time friend', 10)}
    assert styles['gemma3-4b']['counts'] == {'emoji': 10, 'shout': 8, 'slang': 6, 'caps': 2, 'ask': 1, 'plain': 0, 'care': 1, 'long': 0}
    assert styles['phi4-mini']['counts'] == {'emoji': 5, 'shout': 5, 'slang': 3, 'caps': 0, 'ask': 2, 'plain': 2, 'care': 1, 'long': 1}
    assert [styles[key]['total'] for key in ('phi4-mini', 'gemma3-4b', 'granite42-3b', 'qwen35-4b')] == [23, 56, -6, -3]


def test_how_it_works_shows_a_profile_per_model_the_label_once_and_no_new_attribute():
    how = archive_pages.render_chatbots(PUBLISHED)
    assert_page_is_safe(how)
    text = visible(how)
    assert text.count(profiles.LABEL) == 1 and text.count('Just for fun') == 1 and 'samples we wrote' not in text
    assert profiles.LABEL == ('Just for fun: our own reading of how each one answers, worked out by counting things like emoji and '
                              'exclamation marks in the answers on this site. It says nothing about what any of them is or can do.')
    assert how.count('<div class="app-fun">') == 4 == how.count('class="fun-kicker mono">Answering style</span>')
    assert '<section class="group" id="answerers">' in how
    assert re.findall(r'<strong class="fun-name">([^<]+)</strong>', how) == ['Class clown', 'Chaos gremlin', 'Good-time friend', 'Good-time friend']
    # The meter: five boxes, one chosen, a text alternative, and the place in words beside it.
    assert how.count('class="fun-meter" role="img" aria-label="Step ') == 4 and how.count('<span class="fun-box') == 20
    assert how.count('<span class="fun-box fun-on">') == 4
    assert 'aria-label="Step 1 of 5 from wild to grown-up, the wild end"' in how
    assert '1 of 5 from wild to grown-up, the wild end · from 10 answers so far' in text and '2 of 5 from wild to grown-up · from 10 answers so far' in text
    assert 'Because: an emoji in 10 of 10 answers; an exclamation mark in 8 of 10 answers.' in text
    # The row keeps its facts.
    assert 'model: phi4-mini' in text and 'asked 19 Nov 2026 · 1 time in all' in text
    # A recorded answer is shown whole and word for word, with its question; the profile adds no data attribute.
    assert ('<a href="c/C008.html">Is cereal a soup?</a></span> <span class="fun-words">Absolutely not, unless you’re into milk-based mush, '
            'LOL! 😂</span>') in how
    answers = {a['text'] for a in PUBLISHED['chatbots']['answers']}
    assert all(archive_pages.html.unescape(words) in answers for words in re.findall(r'<span class="fun-words">(.*?)</span>', how))
    assert 'data-' not in ''.join(re.findall(r'<div class="app-fun">.*?</div>', how))
    for word in ('round', 'psycho', 'leaderboard', 'smartest', 'ranking'):
        assert word not in text.lower(), word


def test_the_profile_is_display_only_and_in_no_data_file():
    before = json.dumps(PUBLISHED, sort_keys=True)
    site = archive_pages.render_site(copy.deepcopy(PUBLISHED), standalone=True)
    assert json.dumps(PUBLISHED, sort_keys=True) == before
    for name, text in site.items():
        if name.startswith('data/') or name.endswith('.json'):
            for word in (*profiles.STEPS, 'Answering style', 'Just for fun'):
                assert word not in text, (name, word)
        if name.endswith('.html') and name != 'chatbots.html':
            assert 'app-fun' not in text and 'Just for fun' not in text, name
    # The front page: one plain text link to the answerers, nothing more.
    front = site['index.html']
    assert front.count('<p class="meet"><a href="chatbots.html#answerers">Meet the answerers</a></p>') == 1 == front.count('class="meet"')
    assert_page_is_safe(front)


def test_refused_skipped_and_hidden_answers_are_not_counted_and_fewer_than_five_is_too_early():
    payload = copy.deepcopy(PUBLISHED)
    mine = [a for a in payload['chatbots']['answers'] if a['chatbot'] == 'gemma3-4b']
    for answer in mine[:3]:                                                # held back by the content check: no words, never shown
        answer.update(state='withheld', reason='payment link', text=None, changed=None)
    assert real_styles(payload)['gemma3-4b']['count'] == 7 and real_styles(payload)['gemma3-4b']['step'] == 1
    mine[3].update(state='no_answer', reason='skipped: the service was down', text=None, changed=None)
    mine[4].update(state='no_answer', reason='empty', text=None, changed=None)
    mine[5].update(state='no_answer', reason='empty', text=None, changed=None)
    style = real_styles(payload)['gemma3-4b']
    assert (style['count'], style['step'], style['name']) == (4, None, 'Too early to tell')
    how = archive_pages.render_chatbots(payload)
    assert_page_is_safe(how)
    assert '<strong class="fun-name fun-early">Too early to tell</strong>' in how and 'From 4 answers so far. We need 5 to tell.' in visible(how)
    assert how.count('<div class="app-fun">') == 4 and how.count('class="fun-meter"') == 3


def test_a_hostile_answer_is_counted_and_escaped_never_run():
    payload = copy.deepcopy(PUBLISHED)
    hostile = '<script>alert("OMG")</script> <img src=x onerror=y> LOL 😂 {{7*7}} &amp;'
    for answer in payload['chatbots']['answers']:
        if answer['chatbot'] == 'phi4-mini':
            answer['text'] = hostile
    style = real_styles(payload)['phi4-mini']
    assert (style['step'], style['counts']['slang'], style['counts']['emoji'], style['pick']) == (1, 10, 10, 0)
    how = archive_pages.render_chatbots(payload)
    assert_page_is_safe(how)
    assert '<script' not in how and '<img src=x' not in how and 'onerror=y&gt;' in how
    assert f'<span class="fun-words">{archive_pages.esc(hostile)}</span>' in how
    assert 'slang like “OMG” in 10 of 10 answers' in how                  # our list's word, never the answer's own spelling
    assert profiles.signals('LoL oMg')['slang_words'] == ['omg', 'lol']


def test_the_human_is_never_profiled_and_a_sample_site_says_its_profiles_are_samples(ready):
    archive.demo_archive()
    payload = archive.public_payload()
    view = archive_pages._View(payload)
    assert set(archive_pages._styles(view)) == {'chat-app-a', 'chat-app-b', 'chat-app-c'}       # no "human" key
    how = archive_pages.render_chatbots(payload)
    assert_page_is_safe(how)
    text = visible(how)
    human = re.search(r'<div class="app-row kind-human">.*?</div>\n', how).group(0)
    assert 'Human (sample)' in human and 'app-fun' not in human and 'fun-' not in human
    assert how.count('<div class="app-fun">') == 3
    assert text.count(profiles.LABEL + profiles.LABEL_SAMPLE) == 1
    assert profiles.LABEL_SAMPLE == ' Here the answers are samples we wrote, so these styles are samples too.'
    assert len(re.findall(r'from \d+ sample answers so far', text)) == 3 and not re.search(r'from \d+ answers so far', text)
    assert text.count('Sample we wrote, not a real AI answer') == 1                              # the page's one sample label, unchanged
    assert not re.search(r'(?i)\bmodels?\b', visible(archive_pages.render_chatbots(payload, standalone=True)))   # an app page never says "model"
    # A refusal is published as the fact only, and it is not counted.
    before = archive_pages._styles(view)['chat-app-c'][0]['count']
    refusing = copy.deepcopy(payload)
    first = [a for a in refusing['chatbots']['answers'] if a['chatbot'] == 'chat-app-c' and a['state'] == 'answered'
             and a['run'] == max(x['run'] for x in refusing['chatbots']['answers'] if x['chatbot'] == 'chat-app-c')][:2]
    for answer in first:
        answer.update(refused=True, state='no_answer', reason='refused', text=None, changed=None)
    assert archive_pages._styles(archive_pages._View(refusing))['chat-app-c'][0]['count'] == before - 2
    front = archive_pages.render_index(payload)
    assert front.count('<a href="chatbots.html#answerers">Meet the answerers</a>') == 1


def test_stylesheet_rules_for_the_profile_and_the_docs():
    css = (STATIC / 'archive.css').read_text(encoding='utf-8')
    for rule in ('.app-fun {', '.fun-note {', '.fun-name {', '.fun-box {', '.fun-on {', '.fun-words {', '.meet {'):
        assert rule in css, rule
    assert '.fun-box { border: 1px solid CanvasText;' in css                                     # forced colours keep the boxes
    assert '.fun-on { height: 1.125rem;' in css                                                  # the chosen box is taller too, not only coloured
    doc = (REPO / 'docs' / 'ANSWERS-OVER-TIME.md').read_text(encoding='utf-8')
    for phrase in ('Answering style', 'Chaos gremlin', 'The grown-up', 'English only', 'Too early to tell', 'live_genesis/profiles.py',
                   'display only'):
        assert phrase in doc, phrase


def test_whatever_is_published_today_gets_a_style_or_too_early_and_the_page_renders():
    """The live `published/` folder changes every month, so nothing here pins a figure: only the rules."""
    if not LIVE.exists():
        pytest.skip('nothing is published')
    payload = json.loads(LIVE.read_text(encoding='utf-8'))
    styles = real_styles(payload)
    assert styles and all(key != 'human' for key in styles)
    for style in styles.values():
        assert style['count'] >= 0 and (style.get('step') in (1, 2, 3, 4, 5) or style['count'] < 5)
    how = archive_pages.render_chatbots(payload)
    assert 'Just for fun' in how and '<script' not in how
