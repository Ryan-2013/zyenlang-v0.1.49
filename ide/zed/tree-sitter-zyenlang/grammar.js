// Tree-sitter grammar for ZyenLang v0.1.x

const PREC = {
  assignment: 1,
  logical_or: 2,
  logical_and: 3,
  equality: 4,
  relational: 5,
  additive: 6,
  multiplicative: 7,
  unary: 8,
  call: 9,
  member: 10,
};

module.exports = grammar({
  name: 'zyenlang',

  extras: $ => [
    /\s/,
    $.line_comment,
  ],

  word: $ => $.identifier,

  rules: {
    source_file: $ => repeat(choice(
      $.import_statement,
      $.struct_definition,
      $.class_definition,
      $.function_definition,
      $.statement,
    )),

    line_comment: _ => token(seq('//', /.*/)),

    import_statement: $ => seq(
      'import',
      field('path', choice($.string, $.angle_import)),
      optional(seq('as', field('alias', $.namespace_identifier))),
      ';'
    ),

    angle_import: $ => seq('<', field('path', $.import_path), '>'),
    import_path: _ => token(/[A-Za-z_][A-Za-z0-9_\/-]*/),
    namespace_identifier: _ => /[A-Za-z_][A-Za-z0-9_]*/,

    function_definition: $ => seq(
      'fn',
      field('name', $.identifier),
      $.parameter_list,
      optional(seq('->', field('return_type', $.type))),
      field('body', $.block)
    ),

    parameter_list: $ => seq('(', optional(commaSep($.parameter)), ')'),
    parameter: $ => seq(
      field('name', $.identifier),
      optional(seq(':', field('type', $.type)))
    ),

    struct_definition: $ => seq(
      'struct',
      field('name', $.identifier),
      '{',
      repeat($.field_declaration),
      '}'
    ),

    class_definition: $ => seq(
      'class',
      field('name', $.identifier),
      '{',
      repeat(choice($.field_declaration, $.function_definition)),
      '}'
    ),

    field_declaration: $ => seq(
      field('name', $.identifier),
      ':',
      field('type', $.type),
      ';'
    ),

    block: $ => seq('{', repeat($.statement), '}'),

    statement: $ => choice(
      $.let_statement,
      $.const_statement,
      $.set_statement,
      $.return_statement,
      $.if_statement,
      $.for_statement,
      $.break_statement,
      $.pass_statement,
      $.expression_statement,
    ),

    let_statement: $ => seq('let', optional('*'), field('name', $.identifier), optional(seq(':', field('type', $.type))), optional(seq('=', field('value', $.expression))), ';'),
    const_statement: $ => seq('const', optional('*'), field('name', $.identifier), optional(seq(':', field('type', $.type))), '=', field('value', $.expression), ';'),
    set_statement: $ => seq('set', field('target', choice($.identifier, $.field_expression, $.pointer_expression)), '=', field('value', $.expression), ';'),
    return_statement: $ => seq('return', optional($.expression), ';'),
    break_statement: _ => seq('break', ';'),
    pass_statement: _ => seq('pass', ';'),
    expression_statement: $ => seq($.expression, ';'),

    if_statement: $ => seq(
      'if',
      field('condition', $.expression),
      field('consequence', $.block),
      optional(seq('else', field('alternative', choice($.block, $.if_statement))))
    ),

    for_statement: $ => seq(
      'for',
      '(',
      optional(choice($.for_let, $.for_set, $.expression)),
      ';',
      optional($.expression),
      ';',
      optional(choice($.for_set, $.postfix_expression, $.expression)),
      ')',
      field('body', $.block)
    ),

    for_let: $ => seq('let', field('name', $.identifier), optional(seq(':', field('type', $.type))), optional(seq('=', field('value', $.expression)))),
    for_set: $ => seq('set', field('target', choice($.identifier, $.field_expression)), '=', field('value', $.expression)),

    type: $ => choice(
      $.primitive_type,
      $.type_identifier,
      $.generic_type,
    ),
    primitive_type: _ => choice('int', 'float', 'bool', 'str', 'ptr', 'void'),
    type_identifier: _ => /[A-Z][A-Za-z0-9_]*/,
    generic_type: $ => seq($.primitive_type, '<', $.type, '>'),

    expression: $ => choice(
      $.literal,
      $.identifier,
      $.field_expression,
      $.call_expression,
      $.pointer_expression,
      $.address_expression,
      $.unary_expression,
      $.binary_expression,
      $.postfix_expression,
      $.struct_literal,
      $.parenthesized_expression,
    ),

    literal: $ => choice($.number, $.string, $.boolean, $.none),
    number: _ => token(choice(/\d+\.\d+/, /\d+/)),
    string: _ => token(seq('"', repeat(choice(/[^"\\]/, /\\./)), '"')),
    boolean: _ => choice('true', 'false'),
    none: _ => 'None',
    identifier: _ => /[A-Za-z_][A-Za-z0-9_]*/,

    parenthesized_expression: $ => seq('(', $.expression, ')'),
    pointer_expression: $ => prec(PREC.unary, seq('*', $.expression)),
    address_expression: $ => prec(PREC.unary, seq('&', $.expression)),
    unary_expression: $ => prec(PREC.unary, seq(choice('!', '-', '+'), $.expression)),

    binary_expression: $ => choice(
      ...[
        ['||', PREC.logical_or],
        ['&&', PREC.logical_and],
        ['==', PREC.equality],
        ['!=', PREC.equality],
        ['<', PREC.relational],
        ['<=', PREC.relational],
        ['>', PREC.relational],
        ['>=', PREC.relational],
        ['+', PREC.additive],
        ['-', PREC.additive],
        ['*', PREC.multiplicative],
        ['/', PREC.multiplicative],
        ['%', PREC.multiplicative],
      ].map(([operator, precedence]) =>
        prec.left(precedence, seq(field('left', $.expression), operator, field('right', $.expression)))
      )
    ),

    call_expression: $ => prec(PREC.call, seq(
      field('function', choice($.identifier, $.field_expression)),
      $.argument_list
    )),

    argument_list: $ => seq('(', optional(commaSep($.expression)), ')'),

    field_expression: $ => prec(PREC.member, seq(
      field('object', $.expression),
      '.',
      field('field', $.identifier)
    )),

    postfix_expression: $ => prec(PREC.call, seq(
      field('target', $.identifier),
      choice('++', '--', seq('+=', $.expression), seq('-=', $.expression))
    )),

    struct_literal: $ => seq(
      field('type', $.type_identifier),
      '{',
      optional(commaSep($.field_initializer)),
      optional(','),
      '}'
    ),

    field_initializer: $ => seq(field('name', $.identifier), ':', field('value', $.expression)),
  }
});

function commaSep(rule) {
  return seq(rule, repeat(seq(',', rule)), optional(','));
}
